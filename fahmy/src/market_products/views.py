from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import ProtectedError
from django.db.models import Count, F, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import is_customer, is_producer
from market_alerts.models import Notification

from .forms import (
    FarmStoryForm,
    ProducerReviewResponseForm,
    ProductForm,
    RecipeForm,
    ReviewForm,
)
from .models import FarmStory, FavouriteRecipe, Product, Recipe, Review
from .services import (
    add_producer_response,
    average_product_rating,
    create_verified_review,
    get_market_weather,
    producer_can_manage_review,
    published_product_recipes,
    published_producer_recipes,
    published_producer_stories,
    recipe_is_favourited_by_user,
    user_can_review_product,
)


def _producer_directory_queryset(query=""):
    producers = (
        get_user_model()
        .objects.filter(role="PRODUCER", products__is_active=True)
        .annotate(
            active_product_count=Count("products", filter=Q(products__is_active=True), distinct=True),
            published_recipe_count=Count(
                "recipes",
                filter=Q(
                    recipes__status=Recipe.Status.PUBLISHED,
                    recipes__moderation_status=Recipe.ModerationStatus.APPROVED,
                ),
                distinct=True,
            ),
            published_story_count=Count(
                "farm_stories",
                filter=Q(
                    farm_stories__status=FarmStory.Status.PUBLISHED,
                    farm_stories__moderation_status=FarmStory.ModerationStatus.APPROVED,
                ),
                distinct=True,
            ),
        )
        .filter(active_product_count__gt=0)
    )
    if query:
        producers = producers.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(business_name__icontains=query)
            | Q(products__name__icontains=query)
            | Q(products__description__icontains=query)
        ).distinct()
    return producers.order_by("business_name", "username")


PRODUCER_DIRECTORY_DETAILS = {
    "Green Farm Co": {
        "image_url": "https://images.unsplash.com/photo-1500937386664-56d1dfef3854?auto=format&fit=crop&w=1200&q=80",
        "brief": "Seasonal fruit, vegetables, and free range eggs from a Bristol growing team.",
        "description": (
            "Green Farm Co focuses on fresh, practical produce for weekly cooking: orchard fruit, salad crops, "
            "roots, greens, and eggs. Their range changes with the season, with clear availability notes so "
            "customers can plan around local harvests."
        ),
    },
    "Fresh Dairy House": {
        "image_url": "https://images.unsplash.com/photo-1528750997573-59b89d56f4f7?auto=format&fit=crop&w=1200&q=80",
        "brief": "Small-batch dairy staples for breakfast, baking, and everyday cooking.",
        "description": (
            "Fresh Dairy House supplies milk, cultured dairy, butter, cream, and cheeses in sensible pack sizes. "
            "The range is designed for households, cafes, and community groups that need reliable chilled staples."
        ),
    },
    "Bakers Corner": {
        "image_url": "https://images.unsplash.com/photo-1509440159596-0249088772ff?auto=format&fit=crop&w=1200&q=80",
        "brief": "Fresh loaves, rolls, pastries, and sharing bakes made for Bristol deliveries.",
        "description": (
            "Bakers Corner offers everyday bread alongside breakfast pastries and sweet bakes. Products are listed "
            "by pack size so customers can choose between single loaves, rolls for lunches, and boxes for groups."
        ),
    },
}

DEFAULT_PRODUCER_DIRECTORY_DETAIL = {
    "image_url": "https://images.unsplash.com/photo-1517048676732-d65bc937f952?auto=format&fit=crop&w=1200&q=80",
    "brief": "Local producer supplying Bristol customers through the marketplace.",
    "description": "Browse this producer's products, recipes, and farm stories in one place.",
}


def _attach_producer_directory_details(producers):
    for producer in producers:
        name = producer.business_name or producer.username
        detail = PRODUCER_DIRECTORY_DETAILS.get(name, DEFAULT_PRODUCER_DIRECTORY_DETAIL)
        producer.directory_image_url = detail["image_url"]
        producer.directory_brief = detail["brief"]
        producer.directory_description = detail["description"]
        address_parts = [
            (producer.address or "").strip(),
            (producer.postcode or "").strip(),
        ]
        producer.directory_address = ", ".join(part for part in address_parts if part)
    return producers


def _seasonal_availability_q(reference_date=None):
    month = (reference_date or timezone.localdate()).month
    q = Q(seasonal_availability="all_year")

    for season, (start_month, end_month) in Product.SEASON_MONTHS.items():
        if Product._month_in_range(month, start_month, end_month):
            q |= Q(seasonal_availability=season)

    q |= Q(
        season_start_month__lte=month,
        season_end_month__gte=month,
    )
    q |= Q(
        season_start_month__gt=F("season_end_month"),
    ) & (Q(season_start_month__lte=month) | Q(season_end_month__gte=month))
    return q


def discovery(request):
    q = (request.GET.get("q") or "").strip()
    raw_available = request.GET.get("available")
    availability = (raw_available or "both").strip()
    legacy_in_stock_values = {"1", "true", "True", "yes", "on"}
    if availability in legacy_in_stock_values:
        availability = "in_stock"
    valid_availability_filters = {"active", "in_stock", "in_season", "both"}
    if availability not in valid_availability_filters:
        availability = "both"
    category = (request.GET.get("category") or "").strip()
    valid_categories = {value for value, _label in Product.CATEGORY_CHOICES}
    avoid_categories = [
        value
        for value in request.GET.getlist("avoid_categories")
        if value in valid_categories
    ]
    organic = request.GET.get("organic")
    season = (request.GET.get("season") or "").strip()
    allergen_free = (request.GET.get("allergen_free") or "").strip()
    max_food_miles = (request.GET.get("max_food_miles") or "").strip()
    min_price = (request.GET.get("min_price") or "").strip()
    max_price = (request.GET.get("max_price") or "").strip()
    sort = (request.GET.get("sort") or "name").strip()

    qs = Product.objects.select_related("producer").filter(is_active=True)

    in_season_q = _seasonal_availability_q()

    if availability == "in_stock":
        qs = qs.filter(stock_quantity__gt=0)
    elif availability == "in_season":
        qs = qs.filter(in_season_q)
    elif availability == "both":
        qs = qs.filter(stock_quantity__gt=0).filter(in_season_q)

    if category:
        qs = qs.filter(category=category)

    if avoid_categories:
        qs = qs.exclude(category__in=avoid_categories)
        for avoid in avoid_categories:
            qs = qs.exclude(allergens__icontains=avoid)
            if avoid in Product.AVOID_CATEGORY_KEYWORDS:
                for term in Product.AVOID_CATEGORY_KEYWORDS[avoid]:
                    qs = qs.exclude(
                        Q(allergens__icontains=term)
                        | Q(name__icontains=term)
                        | Q(description__icontains=term)
                    )

    if organic in ("1", "true", "True", "yes", "on"):
        qs = qs.filter(is_organic=True)
    elif organic in ("0", "false", "False", "no", "off"):
        qs = qs.filter(is_organic=False)

    if season:
        qs = qs.filter(seasonal_availability=season)

    if allergen_free:
        qs = qs.exclude(allergens__icontains=allergen_free)

    if max_food_miles:
        try:
            qs = qs.filter(food_miles__lte=Decimal(max_food_miles))
        except (InvalidOperation, ValueError):
            messages.error(request, "Food miles must be a number.")

    if min_price:
        try:
            qs = qs.filter(price__gte=Decimal(min_price))
        except (InvalidOperation, ValueError):
            messages.error(request, "Minimum price must be a number.")

    if max_price:
        try:
            qs = qs.filter(price__lte=Decimal(max_price))
        except (InvalidOperation, ValueError):
            messages.error(request, "Maximum price must be a number.")

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(category__icontains=q)
            | Q(allergens__icontains=q)
            | Q(producer__username__icontains=q)
            | Q(producer__email__icontains=q)
            | Q(producer__business_name__icontains=q)
            | Q(seasonal_availability__icontains=q)
        )

    sort_options = {
        "name": "name",
        "price_asc": "price",
        "price_desc": "-price",
        "food_miles": "food_miles",
        "newest": "-id",
    }
    qs = qs.order_by(sort_options.get(sort, "name"), "id")

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    filters = {
        "q": q,
        "available": availability,
        "category": category,
        "avoid_categories": avoid_categories,
        "organic": organic or "",
        "season": season,
        "allergen_free": allergen_free,
        "max_food_miles": max_food_miles,
        "min_price": min_price,
        "max_price": max_price,
        "sort": sort,
    }
    has_advanced_filters = any(
        [
            category,
            avoid_categories,
            organic in ("1", "true", "True", "yes", "on", "0", "false", "False", "no", "off"),
            max_food_miles,
            min_price,
            max_price,
            sort != "name",
            availability != "both",
        ]
    )

    return render(
        request,
        "market_products/discovery.html",
        {
            "page_obj": page_obj,
            "filters": filters,
            "q": q,
            "available": availability,
            "categories": Product.CATEGORY_CHOICES,
            "avoided_category_labels": [
                label
                for value, label in Product.CATEGORY_CHOICES
                if value in avoid_categories
            ],
            "has_advanced_filters": has_advanced_filters,
            "seasons": Product.SEASON_CHOICES,
            "pagination_query": pagination_params.urlencode(),
            "sort": sort,
        },
    )


def producer_directory(request):
    q = (request.GET.get("q") or "").strip()
    producers = _attach_producer_directory_details(list(_producer_directory_queryset(q)))
    return render(
        request,
        "market_products/producer_directory.html",
        {
            "producers": producers,
            "q": q,
        },
    )


def product_detail(request, pk: int):
    product = get_object_or_404(
        Product.objects.select_related("producer").prefetch_related("reviews__user", "recipes"),
        pk=pk,
        is_active=True,
    )
    existing_review = None
    can_review = False
    if request.user.is_authenticated:
        existing_review = product.reviews.filter(user=request.user).first()
        can_review = existing_review is None and user_can_review_product(request.user, product)

    reviews = (
        product.reviews.filter(moderation_status=Review.ModerationStatus.PUBLISHED)
        .select_related("user")
        .order_by("-created_at")
    )
    linked_recipes = list(published_product_recipes(product)[:3])
    producer_stories = list(published_producer_stories(product.producer)[:3])

    return render(
        request,
        "market_products/product_detail.html",
        {
            "product": product,
            "reviews": reviews,
            "average_rating": average_product_rating(product),
            "can_review": can_review,
            "existing_review": existing_review,
            "review_form": ReviewForm(),
            "linked_recipes": linked_recipes,
            "producer_stories": producer_stories,
            "market_weather": get_market_weather(),
        },
    )


@login_required
def submit_review(request, pk: int):
    if request.method != "POST":
        return redirect("product_detail", pk=pk)

    if not is_customer(request.user):
        raise PermissionDenied("Customer access required.")

    product = get_object_or_404(Product, pk=pk, is_active=True)
    form = ReviewForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())
        create_verified_review(
            user=request.user,
            product=product,
            rating=form.cleaned_data["rating"],
            comment=form.cleaned_data["comment"],
        )
        review = Review.objects.get(user=request.user, product=product)
        review.is_anonymous = form.cleaned_data["is_anonymous"]
        review.save(update_fields=["is_anonymous", "updated_at"])
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Review submitted.")
    return redirect("product_detail", pk=pk)


@login_required
def producer_respond_review(request, review_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    review = get_object_or_404(
        Review.objects.select_related("product", "product__producer", "user"),
        id=review_id,
    )
    if not producer_can_manage_review(request.user, review):
        raise PermissionDenied("You can only respond to reviews on your own products.")

    form = ProducerReviewResponseForm(request.POST, instance=review)
    if form.is_valid():
        add_producer_response(
            review=review,
            producer=request.user,
            response_text=form.cleaned_data["producer_response"],
        )
        messages.success(request, "Response saved.")
    else:
        messages.error(request, form.errors.as_text())

    return redirect(request.POST.get("next") or "producer_content_dashboard")


@login_required
def producer_product_list(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    products = Product.objects.filter(producer=request.user).order_by("-id")
    active_count = products.filter(is_active=True).count()
    low_stock_count = sum(1 for product in products if product.is_low_stock)
    return render(
        request,
        "market_products/producer_product_list.html",
        {
            "products": products,
            "active_count": active_count,
            "low_stock_count": low_stock_count,
        },
    )


@login_required
def producer_product_create(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            return redirect("producer_product_list")
    else:
        form = ProductForm()

    return render(
        request,
        "market_products/producer_product_form.html",
        {
            "form": form,
            "page_title": "Add Product",
            "submit_label": "Create Product",
        },
    )


@login_required
def producer_product_edit(request, pk: int):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save()
            if not product.is_low_stock:
                Notification.objects.filter(
                    user=request.user,
                    product=product,
                    type=Notification.Type.LOW_STOCK,
                    is_resolved=False,
                ).update(is_resolved=True, resolved_at=timezone.now())
            return redirect("producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "market_products/producer_product_form.html",
        {
            "form": form,
            "page_title": "Edit Product",
            "submit_label": "Save Changes",
            "product": product,
        },
    )


@login_required
def producer_product_delete(request, pk: int):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")
    if request.method != "POST":
        return HttpResponseBadRequest("Product deletion must be submitted from the product list.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)
    product_name = product.name
    from market_payments.models import CartItem
    from market_finance.models import RecurringOrder

    removed_from_carts = CartItem.objects.filter(product=product).delete()[0]
    recurring_orders_updated = 0
    for recurring_order in RecurringOrder.objects.all():
        data = dict(recurring_order.template_order_data or {})
        items = data.get("items") or []
        kept_items = [item for item in items if str(item.get("product_id")) != str(product.id)]
        if len(kept_items) == len(items):
            continue
        recurring_orders_updated += 1
        if kept_items:
            data["items"] = kept_items
            recurring_order.template_order_data = data
            recurring_order.save(update_fields=["template_order_data", "updated_at"])
        else:
            recurring_order.delete()

    try:
        product.delete()
        if removed_from_carts:
            messages.success(request, f"{product_name} has been deleted and removed from active baskets.")
        elif recurring_orders_updated:
            messages.success(request, f"{product_name} has been deleted and removed from repeat orders.")
        else:
            messages.success(request, f"{product_name} has been deleted.")
    except ProtectedError:
        product.is_active = False
        product.save(update_fields=["is_active"])
        messages.warning(request, f"{product_name} is linked to protected records, so it has been hidden from customers.")
    return redirect("producer_product_list")


@login_required
def producer_content_dashboard(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    recipes = Recipe.objects.filter(producer=request.user).prefetch_related("products").order_by("-updated_at")
    stories = FarmStory.objects.filter(producer=request.user).order_by("-updated_at")
    reviews = (
        Review.objects.filter(product__producer=request.user)
        .select_related("product", "user")
        .order_by("-created_at")
    )
    return render(
        request,
        "market_products/producer_content_dashboard.html",
        {
            "recipes": recipes,
            "stories": stories,
            "reviews": reviews,
        },
    )


@login_required
def producer_recipe_create(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    if request.method == "POST":
        form = RecipeForm(request.POST, producer=request.user)
        if form.is_valid():
            recipe = form.save(commit=False)
            recipe.producer = request.user
            if recipe.status == Recipe.Status.PUBLISHED and recipe.published_at is None:
                from django.utils import timezone
                recipe.published_at = timezone.now()
            recipe.save()
            form.save_m2m()
            messages.success(request, "Recipe saved.")
            return redirect("producer_content_dashboard")
    else:
        form = RecipeForm(producer=request.user)

    return render(
        request,
        "market_products/producer_content_form.html",
        {
            "form": form,
            "page_title": "Create Recipe",
            "submit_label": "Save Recipe",
            "content_type": "recipe",
        },
    )


@login_required
def producer_recipe_edit(request, recipe_id: int):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    recipe = get_object_or_404(Recipe, id=recipe_id, producer=request.user)
    if request.method == "POST":
        form = RecipeForm(request.POST, instance=recipe, producer=request.user)
        if form.is_valid():
            recipe = form.save(commit=False)
            if recipe.status == Recipe.Status.PUBLISHED and recipe.published_at is None:
                from django.utils import timezone
                recipe.published_at = timezone.now()
            recipe.save()
            form.save_m2m()
            messages.success(request, "Recipe updated.")
            return redirect("producer_content_dashboard")
    else:
        form = RecipeForm(instance=recipe, producer=request.user)

    return render(
        request,
        "market_products/producer_content_form.html",
        {
            "form": form,
            "page_title": "Edit Recipe",
            "submit_label": "Save Recipe",
            "content_type": "recipe",
        },
    )


@login_required
def producer_story_create(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    if request.method == "POST":
        form = FarmStoryForm(request.POST)
        if form.is_valid():
            story = form.save(commit=False)
            story.producer = request.user
            if story.status == FarmStory.Status.PUBLISHED and story.published_at is None:
                from django.utils import timezone
                story.published_at = timezone.now()
            story.save()
            messages.success(request, "Farm story saved.")
            return redirect("producer_content_dashboard")
    else:
        form = FarmStoryForm()

    return render(
        request,
        "market_products/producer_content_form.html",
        {
            "form": form,
            "page_title": "Create Farm Story",
            "submit_label": "Save Story",
            "content_type": "story",
        },
    )


@login_required
def producer_story_edit(request, story_id: int):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    story = get_object_or_404(FarmStory, id=story_id, producer=request.user)
    if request.method == "POST":
        form = FarmStoryForm(request.POST, instance=story)
        if form.is_valid():
            story = form.save(commit=False)
            if story.status == FarmStory.Status.PUBLISHED and story.published_at is None:
                from django.utils import timezone
                story.published_at = timezone.now()
            story.save()
            messages.success(request, "Farm story updated.")
            return redirect("producer_content_dashboard")
    else:
        form = FarmStoryForm(instance=story)

    return render(
        request,
        "market_products/producer_content_form.html",
        {
            "form": form,
            "page_title": "Edit Farm Story",
            "submit_label": "Save Story",
            "content_type": "story",
        },
    )


def recipe_detail(request, recipe_id: int):
    recipe = get_object_or_404(
        Recipe.objects.select_related("producer").prefetch_related("products"),
        id=recipe_id,
        status=Recipe.Status.PUBLISHED,
        moderation_status=Recipe.ModerationStatus.APPROVED,
    )
    return render(
        request,
        "market_products/recipe_detail.html",
        {
            "recipe": recipe,
            "is_favourited": recipe_is_favourited_by_user(recipe, request.user),
        },
    )


def story_detail(request, story_id: int):
    story = get_object_or_404(
        FarmStory,
        id=story_id,
        status=FarmStory.Status.PUBLISHED,
        moderation_status=FarmStory.ModerationStatus.APPROVED,
    )
    return render(
        request,
        "market_products/story_detail.html",
        {"story": story},
    )


def producer_profile(request, producer_id: int):
    producer_user = get_object_or_404(get_user_model(), id=producer_id)
    products = Product.objects.filter(producer=producer_user, is_active=True).order_by("name")
    recipes = published_producer_recipes(producer_user)
    stories = published_producer_stories(producer_user)

    favourite_ids = set()
    if request.user.is_authenticated:
        favourite_ids = set(
            FavouriteRecipe.objects.filter(customer=request.user, recipe__in=recipes).values_list("recipe_id", flat=True)
        )

    return render(
        request,
        "market_products/producer_profile.html",
        {
            "producer": producer_user,
            "products": products,
            "recipes": recipes,
            "stories": stories,
            "favourite_ids": favourite_ids,
        },
    )


@login_required
def toggle_favourite_recipe(request, recipe_id: int):
    if request.method != "POST":
        raise PermissionDenied("POST required.")

    if not is_customer(request.user):
        raise PermissionDenied("Customer access required.")

    recipe = get_object_or_404(
        Recipe,
        id=recipe_id,
        status=Recipe.Status.PUBLISHED,
        moderation_status=Recipe.ModerationStatus.APPROVED,
    )
    favourite, created = FavouriteRecipe.objects.get_or_create(customer=request.user, recipe=recipe)
    if created:
        messages.success(request, "Recipe saved to favourites.")
    else:
        favourite.delete()
        messages.info(request, "Recipe removed from favourites.")
    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("recipe_detail", recipe_id=recipe.id)

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder
from market_payments.models import Order as PaymentOrder
from market_payments.models import OrderItem as PaymentOrderItem
from market_payments.models import Payment

from .models import FarmStory, FavouriteRecipe, Product, Recipe, Review
from .services import average_product_rating, create_verified_review, user_can_review_product


class DiscoverySearchTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.producer = user_model.objects.create_user(
            username="search_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Search Farm",
        )
        self.other_producer = user_model.objects.create_user(
            username="bakery_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Search Bakery",
        )
        self.apples = Product.objects.create(
            name="Organic Apples",
            description="Fresh autumn fruit",
            producer=self.producer,
            category="fruit_veg",
            image_url="https://example.com/apples.jpg",
            price=Decimal("4.00"),
            stock_quantity=12,
            is_active=True,
            is_organic=True,
            food_miles=Decimal("6.5"),
            seasonal_availability="autumn",
            allergens="",
        )
        self.bread = Product.objects.create(
            name="Seeded Bread",
            description="Bakery loaf",
            producer=self.other_producer,
            category="bakery",
            image_url="https://example.com/bread.jpg",
            price=Decimal("3.00"),
            stock_quantity=8,
            is_active=True,
            is_organic=False,
            food_miles=Decimal("2.0"),
            seasonal_availability="all_year",
            allergens="Gluten",
        )
        self.milk = Product.objects.create(
            name="Whole Milk",
            description="Local dairy",
            producer=self.producer,
            category="dairy",
            image_url="https://example.com/milk.jpg",
            price=Decimal("2.00"),
            stock_quantity=0,
            is_active=True,
            is_organic=False,
            food_miles=Decimal("15.0"),
            seasonal_availability="all_year",
            allergens="Milk",
        )
        self.eggs = Product.objects.create(
            name="Free Range Eggs",
            description="Local egg tray",
            producer=self.producer,
            category="eggs",
            image_url="https://example.com/eggs.jpg",
            price=Decimal("5.00"),
            stock_quantity=6,
            is_active=True,
            is_organic=True,
            food_miles=Decimal("4.0"),
            seasonal_availability="all_year",
            allergens="Eggs",
        )

    def test_search_matches_product_description_and_producer(self):
        response = self.client.get(reverse("discovery_alt"), {"q": "autumn"})
        self.assertContains(response, "Organic Apples")
        self.assertNotContains(response, "Seeded Bread")

        response = self.client.get(reverse("discovery_alt"), {"q": "Search Bakery"})
        self.assertContains(response, "Seeded Bread")
        self.assertNotContains(response, "Whole Milk")

    def test_category_filter_browses_products_by_category(self):
        response = self.client.get(reverse("discovery_alt"), {"category": "bakery"})
        self.assertContains(response, "Seeded Bread")
        self.assertNotContains(response, "Organic Apples")

        response = self.client.get(reverse("discovery_alt"), {"category": "eggs"})
        self.assertContains(response, "Free Range Eggs")
        self.assertContains(response, "Eggs")
        self.assertNotContains(response, "Whole Milk")

    def test_available_filter_hides_out_of_stock_products(self):
        response = self.client.get(reverse("discovery_alt"), {"available": "1"})
        self.assertContains(response, "Organic Apples")
        self.assertNotContains(response, "Whole Milk")

    def test_organic_filter_only_shows_organic_products(self):
        response = self.client.get(reverse("discovery_alt"), {"organic": "1"})
        self.assertContains(response, "Organic Apples")
        self.assertNotContains(response, "Seeded Bread")

    def test_allergen_free_filter_excludes_matching_allergens(self):
        response = self.client.get(reverse("discovery_alt"), {"allergen_free": "gluten"})
        self.assertContains(response, "Organic Apples")
        self.assertNotContains(response, "Seeded Bread")

    def test_avoid_categories_filter_excludes_multiple_categories(self):
        response = self.client.get(
            reverse("discovery_alt"),
            {"avoid_categories": ["bakery", "dairy"]},
        )

        self.assertContains(response, "Organic Apples")
        self.assertContains(response, "Free Range Eggs")
        self.assertNotContains(response, "Seeded Bread")
        self.assertNotContains(response, "Whole Milk")
        self.assertContains(response, "Avoiding Bakery")
        self.assertContains(response, "Avoiding Dairy")

    def test_season_filter_includes_selected_season_and_all_year(self):
        response = self.client.get(reverse("discovery_alt"), {"season": "autumn"})
        self.assertContains(response, "Organic Apples")
        self.assertContains(response, "Seeded Bread")
        self.assertContains(response, "Whole Milk")

        response = self.client.get(reverse("discovery_alt"), {"season": "summer"})
        self.assertNotContains(response, "Organic Apples")
        self.assertContains(response, "Seeded Bread")

    def test_food_miles_and_price_filters_limit_results(self):
        response = self.client.get(reverse("discovery_alt"), {"max_food_miles": "7", "max_price": "3.50"})
        self.assertContains(response, "Seeded Bread")
        self.assertNotContains(response, "Organic Apples")
        self.assertNotContains(response, "Whole Milk")

    def test_discovery_shows_test_case_metadata(self):
        response = self.client.get(reverse("discovery_alt"), {"q": "Organic Apples"})
        self.assertContains(response, "Organic")
        self.assertContains(response, "6.5 food miles")
        self.assertContains(response, "Autumn")


class VerifiedReviewWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="customer_review",
            password="secret123",
            role=user_model.Role.CUSTOMER,
            address="1 Review Street",
            phone="07000000000",
        )
        self.other_customer = user_model.objects.create_user(
            username="other_customer_review",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer = user_model.objects.create_user(
            username="producer_review",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Review Farm",
        )
        self.other_producer = user_model.objects.create_user(
            username="other_producer_review",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Other Review Farm",
        )
        self.product = Product.objects.create(
            name="Review Apples",
            producer=self.producer,
            image_url="https://example.com/review-apples.jpg",
            price=Decimal("3.50"),
            stock_quantity=50,
            is_active=True,
        )
        self.order, self.suborder = self._create_market_order(
            customer=self.customer,
            product=self.product,
            status=ProducerSubOrder.Status.DELIVERED,
        )

    def _create_market_order(self, *, customer, product, status):
        order = Order.objects.create(
            customer=customer,
            status=Order.Status.CREATED,
            total_amount=product.price,
            commission_total=Decimal("0.18"),
            delivery_address=getattr(customer, "address", "") or "Test address",
            customer_phone=getattr(customer, "phone", "") or "07000000001",
        )
        suborder = ProducerSubOrder.objects.create(
            order=order,
            producer=product.producer,
            status=status,
            delivery_date=timezone.now(),
            subtotal=product.price,
            commission_amount=Decimal("0.18"),
            producer_payout_amount=Decimal("3.32"),
        )
        OrderItem.objects.create(
            suborder=suborder,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=1,
        )
        return order, suborder

    def _create_payment_order_for_history(self, *, customer, product, market_order):
        payment_order = PaymentOrder.objects.create(
            user=customer,
            subtotal=product.price,
            commission=Decimal("0.18"),
            total=Decimal("3.68"),
        )
        Payment.objects.create(
            order=payment_order,
            status=Payment.Status.PAID,
            provider="demo_card",
        )
        PaymentOrderItem.objects.create(
            order=payment_order,
            product_name=product.name,
            unit_price=product.price,
            quantity=1,
        )
        market_order.created_at = payment_order.created_at
        market_order.save(update_fields=["created_at"])
        return payment_order

    def test_user_can_review_only_after_delivered_purchase(self):
        pending_order, pending_suborder = self._create_market_order(
            customer=self.other_customer,
            product=self.product,
            status=ProducerSubOrder.Status.CONFIRMED,
        )
        self.assertFalse(user_can_review_product(self.other_customer, self.product))

        pending_suborder.status = ProducerSubOrder.Status.DELIVERED
        pending_suborder.save(update_fields=["status"])
        self.assertTrue(user_can_review_product(self.other_customer, self.product))

    def test_customer_can_submit_verified_review_and_average_updates(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("submit_review", args=[self.product.id]),
            {"rating": "5", "comment": "Excellent apples", "is_anonymous": "on"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review submitted")
        self.assertContains(response, "Average rating:")
        self.assertContains(response, "5.0/5")
        self.assertContains(response, "Anonymous customer")
        self.assertContains(response, "Verified purchase")
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(average_product_rating(self.product), Decimal("5"))

    def test_non_delivered_customer_cannot_submit_review(self):
        undelivered_product = Product.objects.create(
            name="Pears",
            producer=self.producer,
            price=Decimal("4.00"),
            stock_quantity=10,
            is_active=True,
        )
        self._create_market_order(
            customer=self.other_customer,
            product=undelivered_product,
            status=ProducerSubOrder.Status.READY,
        )
        self.client.force_login(self.other_customer)
        response = self.client.post(
            reverse("submit_review", args=[undelivered_product.id]),
            {"rating": "4", "comment": "Too early"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "delivered order")
        self.assertFalse(Review.objects.filter(user=self.other_customer, product=undelivered_product).exists())

    def test_duplicate_reviews_are_blocked(self):
        create_verified_review(
            user=self.customer,
            product=self.product,
            rating=5,
            comment="First review",
        )
        with self.assertRaises(ValidationError):
            create_verified_review(
                user=self.customer,
                product=self.product,
                rating=4,
                comment="Second review",
            )

    def test_order_history_shows_review_entry_for_delivered_order(self):
        payment_order = self._create_payment_order_for_history(
            customer=self.customer,
            product=self.product,
            market_order=self.order,
        )
        self.client.force_login(self.customer)
        response = self.client.get(reverse("market_payments:order_history"))

        self.assertContains(response, f"Order #{payment_order.id}")
        self.assertContains(response, "Write Review")
        self.assertContains(response, "Review Apples is ready for rating")

    def test_order_history_hides_review_link_for_non_delivered_items(self):
        other_product = Product.objects.create(
            name="Review Carrots",
            producer=self.producer,
            price=Decimal("2.50"),
            stock_quantity=20,
            is_active=True,
        )
        order, _suborder = self._create_market_order(
            customer=self.other_customer,
            product=other_product,
            status=ProducerSubOrder.Status.PENDING,
        )
        payment_order = self._create_payment_order_for_history(
            customer=self.other_customer,
            product=other_product,
            market_order=order,
        )
        self.client.force_login(self.other_customer)
        response = self.client.get(reverse("market_payments:order_history"))

        self.assertContains(response, f"Order #{payment_order.id}")
        self.assertContains(response, "can be reviewed after delivery")
        self.assertNotContains(response, "Write Review")

    def test_producer_can_respond_to_review(self):
        review = create_verified_review(
            user=self.customer,
            product=self.product,
            rating=5,
            comment="Really fresh",
        )
        self.client.force_login(self.producer)
        response = self.client.post(
            reverse("producer_respond_review", args=[review.id]),
            {"producer_response": "Thank you for supporting the farm."},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.producer_response, "Thank you for supporting the farm.")

    def test_other_producer_cannot_respond_to_review(self):
        review = create_verified_review(
            user=self.customer,
            product=self.product,
            rating=5,
            comment="Really fresh",
        )
        self.client.force_login(self.other_producer)
        response = self.client.post(
            reverse("producer_respond_review", args=[review.id]),
            {"producer_response": "Not your review"},
        )
        self.assertEqual(response.status_code, 403)

    def test_hidden_reviews_do_not_affect_average_or_display(self):
        Review.objects.create(
            user=self.customer,
            product=self.product,
            rating=1,
            comment="Hidden review",
            moderation_status=Review.ModerationStatus.HIDDEN,
        )
        Review.objects.create(
            user=self.other_customer,
            product=self.product,
            rating=5,
            comment="Visible review",
            moderation_status=Review.ModerationStatus.PUBLISHED,
        )
        response = self.client.get(reverse("product_detail", args=[self.product.id]))
        self.assertContains(response, "5.0/5")
        self.assertContains(response, "Visible review")
        self.assertNotContains(response, "Hidden review")


class ProducerContentTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="recipe_customer",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer = user_model.objects.create_user(
            username="recipe_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Content Farm",
        )
        self.other_producer = user_model.objects.create_user(
            username="recipe_other_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Other Content Farm",
        )
        self.product = Product.objects.create(
            name="Cooking Onions",
            producer=self.producer,
            image_url="https://example.com/onions.jpg",
            price=Decimal("2.20"),
            stock_quantity=30,
            is_active=True,
        )
        self.other_product = Product.objects.create(
            name="Other Farm Eggs",
            producer=self.other_producer,
            image_url="https://example.com/eggs.jpg",
            price=Decimal("4.20"),
            stock_quantity=12,
            is_active=True,
        )

    def test_producer_can_create_recipe_linked_to_owned_products(self):
        self.client.force_login(self.producer)
        response = self.client.post(
            reverse("producer_recipe_create"),
            {
                "title": "Roast Onion Tart",
                "description": "A savoury tart using local onions.",
                "ingredients": "Onions\nButter\nPastry",
                "instructions": "Slice\nRoast\nBake",
                "storage_guidance": "Keep chilled.",
                "freshness_guidance": "Use within two days.",
                "seasonal_tag": Recipe.Season.AUTUMN,
                "image_url": "https://example.com/onion.jpg",
                "status": Recipe.Status.PUBLISHED,
                "products": [self.product.id],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        recipe = Recipe.objects.get(title="Roast Onion Tart")
        self.assertEqual(recipe.producer, self.producer)
        self.assertEqual(list(recipe.products.all()), [self.product])
        self.assertIsNotNone(recipe.published_at)

    def test_recipe_form_does_not_link_other_producers_products(self):
        self.client.force_login(self.producer)
        response = self.client.post(
            reverse("producer_recipe_create"),
            {
                "title": "Mixed Ownership Recipe",
                "description": "desc",
                "ingredients": "ing",
                "instructions": "inst",
                "storage_guidance": "",
                "freshness_guidance": "",
                "seasonal_tag": Recipe.Season.SUMMER,
                "image_url": "",
                "status": Recipe.Status.PUBLISHED,
                "products": [self.product.id, self.other_product.id],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.filter(title="Mixed Ownership Recipe").exists())

    def test_producer_can_create_farm_story(self):
        self.client.force_login(self.producer)
        response = self.client.post(
            reverse("producer_story_create"),
            {
                "title": "Preparing The Autumn Beds",
                "summary": "How the field is prepared.",
                "body": "Long form story body.",
                "educational_content": "Why soil care matters.",
                "seasonal_tag": FarmStory.Season.AUTUMN,
                "image_url": "https://example.com/story.jpg",
                "status": FarmStory.Status.PUBLISHED,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(FarmStory.objects.filter(title="Preparing The Autumn Beds", producer=self.producer).exists())

    def test_customer_cannot_access_producer_content_pages(self):
        self.client.force_login(self.customer)
        protected_urls = [
            reverse("producer_content_dashboard"),
            reverse("producer_recipe_create"),
            reverse("producer_story_create"),
        ]
        for url in protected_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

    def test_recipe_detail_and_product_page_show_linked_recipe(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Onion Soup",
            description="Comforting soup.",
            ingredients="Onions\nStock",
            instructions="Cook slowly.",
            storage_guidance="Refrigerate after cooking.",
            freshness_guidance="Best within 48 hours.",
            seasonal_tag=Recipe.Season.WINTER,
            image_url="https://example.com/onion-soup.jpg",
            status=Recipe.Status.PUBLISHED,
        )
        recipe.products.add(self.product)

        product_response = self.client.get(reverse("product_detail", args=[self.product.id]))
        self.assertContains(product_response, "Recipe Suggestions")
        self.assertContains(product_response, "Onion Soup")
        self.assertContains(product_response, 'src="https://example.com/onion-soup.jpg"')

        recipe_response = self.client.get(reverse("recipe_detail", args=[recipe.id]))
        self.assertContains(recipe_response, "Storage & Freshness")
        self.assertContains(recipe_response, "Cooking Onions")
        self.assertContains(recipe_response, 'src="https://example.com/onion-soup.jpg"')

    def test_product_detail_shows_product_image(self):
        response = self.client.get(reverse("product_detail", args=[self.product.id]))
        self.assertContains(response, 'src="https://example.com/onions.jpg"')

    def test_discovery_shows_product_image(self):
        response = self.client.get(reverse("discovery_alt"))
        self.assertContains(response, 'src="https://example.com/onions.jpg"')

    def test_producer_profile_shows_stories_and_recipes(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Farm Risotto",
            description="Creamy local risotto.",
            ingredients="Rice\nStock",
            instructions="Cook gently.",
            seasonal_tag=Recipe.Season.ALL_YEAR,
            image_url="https://example.com/risotto.jpg",
            status=Recipe.Status.PUBLISHED,
        )
        recipe.products.add(self.product)
        FarmStory.objects.create(
            producer=self.producer,
            title="Spring Seedlings",
            summary="Early spring work.",
            body="Story body.",
            educational_content="Seasonal growing tips.",
            seasonal_tag=FarmStory.Season.SPRING,
            image_url="https://example.com/seedlings.jpg",
            status=FarmStory.Status.PUBLISHED,
        )

        response = self.client.get(reverse("producer_profile", args=[self.producer.id]))
        self.assertContains(response, "Farm Risotto")
        self.assertContains(response, "Spring Seedlings")
        self.assertContains(response, 'src="https://example.com/risotto.jpg"')
        self.assertContains(response, 'src="https://example.com/seedlings.jpg"')

    def test_product_page_shows_story_image_when_story_is_present(self):
        FarmStory.objects.create(
            producer=self.producer,
            title="Harvest Morning",
            summary="Field update.",
            body="Longer farm story body.",
            seasonal_tag=FarmStory.Season.SUMMER,
            image_url="https://example.com/harvest.jpg",
            status=FarmStory.Status.PUBLISHED,
        )
        response = self.client.get(reverse("product_detail", args=[self.product.id]))
        self.assertContains(response, "From The Producer")
        self.assertContains(response, 'src="https://example.com/harvest.jpg"')

    def test_producer_content_forms_render_image_preview_markup(self):
        self.client.force_login(self.producer)
        recipe_response = self.client.get(reverse("producer_recipe_create"))
        story_response = self.client.get(reverse("producer_story_create"))

        self.assertContains(recipe_response, "Image preview")
        self.assertContains(recipe_response, "image-url-preview")
        self.assertContains(story_response, "Image preview")
        self.assertContains(story_response, "image-url-preview")

    def test_producer_product_form_renders_image_preview_markup(self):
        self.client.force_login(self.producer)
        response = self.client.get(reverse("producer_product_create"))
        self.assertContains(response, "Image preview")
        self.assertContains(response, "product-image-preview")

    def test_customer_can_save_favourite_recipe(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Favourite Soup",
            description="Soup",
            ingredients="Onions",
            instructions="Cook",
            seasonal_tag=Recipe.Season.ALL_YEAR,
            status=Recipe.Status.PUBLISHED,
        )
        recipe.products.add(self.product)

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("toggle_favourite_recipe", args=[recipe.id]),
            {"next": reverse("recipe_detail", args=[recipe.id])},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(FavouriteRecipe.objects.filter(customer=self.customer, recipe=recipe).exists())

    def test_recipe_edit_is_limited_to_owner(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Owner Only",
            description="desc",
            ingredients="ing",
            instructions="inst",
            seasonal_tag=Recipe.Season.ALL_YEAR,
        )
        self.client.force_login(self.other_producer)
        response = self.client.get(reverse("producer_recipe_edit", args=[recipe.id]))
        self.assertEqual(response.status_code, 404)

    def test_producer_cannot_favourite_recipe(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Producer Favourite Block",
            description="desc",
            ingredients="ing",
            instructions="inst",
            seasonal_tag=Recipe.Season.ALL_YEAR,
            status=Recipe.Status.PUBLISHED,
        )
        self.client.force_login(self.producer)
        response = self.client.post(reverse("toggle_favourite_recipe", args=[recipe.id]))
        self.assertEqual(response.status_code, 403)

    def test_story_edit_is_limited_to_owner(self):
        story = FarmStory.objects.create(
            producer=self.producer,
            title="Owner Story",
            summary="sum",
            body="body",
            seasonal_tag=FarmStory.Season.ALL_YEAR,
        )
        self.client.force_login(self.other_producer)
        response = self.client.get(reverse("producer_story_edit", args=[story.id]))
        self.assertEqual(response.status_code, 404)

    def test_hidden_recipe_is_not_shown_publicly(self):
        recipe = Recipe.objects.create(
            producer=self.producer,
            title="Hidden Recipe",
            description="desc",
            ingredients="ing",
            instructions="inst",
            seasonal_tag=Recipe.Season.ALL_YEAR,
            status=Recipe.Status.PUBLISHED,
            moderation_status=Recipe.ModerationStatus.HIDDEN,
        )
        recipe.products.add(self.product)
        response = self.client.get(reverse("product_detail", args=[self.product.id]))
        self.assertNotContains(response, "Hidden Recipe")

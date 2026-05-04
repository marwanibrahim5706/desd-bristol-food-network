import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg
from django.utils import timezone

from market_orders.models import ProducerSubOrder

from .models import FarmStory, FavouriteRecipe, Recipe, Review


def user_can_review_product(user, product):
    if not user.is_authenticated:
        return False

    return product.order_items.filter(
        suborder__order__customer=user,
        suborder__status=ProducerSubOrder.Status.DELIVERED,
    ).exists()


def create_verified_review(*, user, product, rating, comment=""):
    """
    Only allow one review per user for products they have already received.
    """
    if not user_can_review_product(user, product):
        raise ValidationError("You can only review products you have received in a delivered order.")

    if Review.objects.filter(user=user, product=product).exists():
        raise ValidationError("You have already reviewed this product.")

    review = Review(
        user=user,
        product=product,
        rating=rating,
        comment=comment,
        verified_purchase=True,
    )
    review.full_clean()
    review.save()
    return review


def average_product_rating(product):
    return product.reviews.filter(
        moderation_status=Review.ModerationStatus.PUBLISHED
    ).aggregate(avg=Avg("rating"))["avg"]


def published_product_recipes(product):
    return product.recipes.filter(
        status=Recipe.Status.PUBLISHED,
        moderation_status=Recipe.ModerationStatus.APPROVED,
    ).select_related("producer")


def published_producer_stories(producer):
    return FarmStory.objects.filter(
        producer=producer,
        status=FarmStory.Status.PUBLISHED,
        moderation_status=FarmStory.ModerationStatus.APPROVED,
    )


def published_producer_recipes(producer):
    return Recipe.objects.filter(
        producer=producer,
        status=Recipe.Status.PUBLISHED,
        moderation_status=Recipe.ModerationStatus.APPROVED,
    ).prefetch_related("products")


def producer_can_manage_review(user, review):
    return user.is_authenticated and getattr(user, "id", None) == review.product.producer_id


def add_producer_response(*, review, producer, response_text):
    if not producer_can_manage_review(producer, review):
        raise PermissionDenied("You can only respond to reviews on your own products.")
    review.producer_response = response_text.strip()
    review.producer_responded_at = timezone.now()
    review.full_clean()
    review.save(update_fields=["producer_response", "producer_responded_at", "updated_at"])
    return review


def recipe_is_favourited_by_user(recipe, user):
    if not user.is_authenticated:
        return False
    return FavouriteRecipe.objects.filter(customer=user, recipe=recipe).exists()


def get_market_weather():
    if not settings.WEATHER_API_KEY:
        return None

    query = urlencode(
        {
            "q": settings.WEATHER_LOCATION,
            "appid": settings.WEATHER_API_KEY,
            "units": "metric",
        }
    )
    url = f"https://api.openweathermap.org/data/2.5/weather?{query}"

    try:
        with urlopen(url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
        return None

    weather = data.get("weather") or [{}]
    main = data.get("main") or {}
    temperature = main.get("temp")
    description = weather[0].get("description")

    if temperature is None or not description:
        return None

    return {
        "location": data.get("name") or settings.WEATHER_LOCATION,
        "temperature": round(float(temperature)),
        "description": description.title(),
    }

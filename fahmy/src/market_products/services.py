from django.core.exceptions import ValidationError
from django.db.models import Avg

from market_orders.models import ProducerSubOrder

from .models import Review


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

    review = Review(user=user, product=product, rating=rating, comment=comment)
    review.full_clean()
    review.save()
    return review


def average_product_rating(product):
    return product.reviews.aggregate(avg=Avg("rating"))["avg"]

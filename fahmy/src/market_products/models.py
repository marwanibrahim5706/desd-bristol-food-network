from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Product(models.Model):
    CATEGORY_CHOICES = [
        ("fruit_veg", "Fruit & Vegetables"),
        ("dairy", "Dairy"),
        ("eggs", "Eggs"),
        ("bakery", "Bakery"),
        ("meat", "Meat"),
        ("drinks", "Drinks"),
        ("other", "Other"),
    ]

    SEASON_CHOICES = [
        ("spring", "Spring"),
        ("summer", "Summer"),
        ("autumn", "Autumn"),
        ("winter", "Winter"),
        ("all_year", "All year"),
    ]

    name = models.CharField(max_length=255)

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
    )

    description = models.TextField(blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="other")
    image_url = models.URLField(blank=True, default="")
    is_organic = models.BooleanField(default=False)
    food_miles = models.DecimalField(max_digits=6, decimal_places=1, blank=True, null=True)
    seasonal_availability = models.CharField(max_length=20, choices=SEASON_CHOICES, default="all_year")

    price = models.DecimalField(max_digits=10, decimal_places=2)

    stock_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)

    is_active = models.BooleanField(default=True)
    allergens = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name

    @property
    def image_fallback_url(self):
        return f"https://placehold.co/1200x800/e5e7eb/111827?text={self.name.replace(' ', '+')}"

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold

    @property
    def published_reviews(self):
        return self.reviews.filter(moderation_status=Review.ModerationStatus.PUBLISHED)


class Review(models.Model):
    class ModerationStatus(models.TextChoices):
        PUBLISHED = "PUBLISHED", "Published"
        HIDDEN = "HIDDEN", "Hidden"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_reviews",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    is_anonymous = models.BooleanField(default=False)
    comment = models.TextField(blank=True, default="")
    verified_purchase = models.BooleanField(default=True)
    moderation_status = models.CharField(
        max_length=20,
        choices=ModerationStatus.choices,
        default=ModerationStatus.PUBLISHED,
    )
    producer_response = models.TextField(blank=True, default="")
    producer_responded_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="unique_review_per_user_product",
            )
        ]

    def __str__(self):
        return f"Review #{self.id} for {self.product}"

    @property
    def display_name(self):
        return "Anonymous customer" if self.is_anonymous else self.user.username


class Recipe(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"

    class Season(models.TextChoices):
        SPRING = "SPRING", "Spring"
        SUMMER = "SUMMER", "Summer"
        AUTUMN = "AUTUMN", "Autumn"
        WINTER = "WINTER", "Winter"
        ALL_YEAR = "ALL_YEAR", "All year"

    class ModerationStatus(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        HIDDEN = "HIDDEN", "Hidden"

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipes",
    )
    products = models.ManyToManyField(Product, related_name="recipes", blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    ingredients = models.TextField()
    instructions = models.TextField()
    storage_guidance = models.TextField(blank=True, default="")
    freshness_guidance = models.TextField(blank=True, default="")
    seasonal_tag = models.CharField(
        max_length=20,
        choices=Season.choices,
        default=Season.ALL_YEAR,
    )
    image_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    moderation_status = models.CharField(
        max_length=20,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.pk and self.products.exclude(producer=self.producer).exists():
            raise ValidationError({"products": "Recipes can only link to products owned by the same producer."})


class FarmStory(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"

    class Season(models.TextChoices):
        SPRING = "SPRING", "Spring"
        SUMMER = "SUMMER", "Summer"
        AUTUMN = "AUTUMN", "Autumn"
        WINTER = "WINTER", "Winter"
        ALL_YEAR = "ALL_YEAR", "All year"

    class ModerationStatus(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        HIDDEN = "HIDDEN", "Hidden"

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_stories",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField()
    body = models.TextField()
    educational_content = models.TextField(blank=True, default="")
    seasonal_tag = models.CharField(
        max_length=20,
        choices=Season.choices,
        default=Season.ALL_YEAR,
    )
    image_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    moderation_status = models.CharField(
        max_length=20,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title


class FavouriteRecipe(models.Model):
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favourite_recipes",
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="favourites",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "recipe"],
                name="unique_favourite_recipe_per_customer",
            )
        ]

    def __str__(self):
        return f"{self.customer} -> {self.recipe}"

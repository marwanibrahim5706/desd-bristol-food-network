from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


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

    AVOID_CATEGORY_KEYWORDS = {
        "fruit_veg": ["fruit", "vegetable", "veg", "salad", "greens", "herb", "courgette", "cucumber", "tomato", "potato"],
        "dairy": ["milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "custard", "kefir", "casein"],
        "eggs": ["egg", "eggs", "brioche", "croissant", "omelette", "mayonnaise", "meringue", "custard", "quiche", "pancake"],
        "bakery": ["bread", "loaf", "roll", "brioche", "baguette", "croissant", "pastry", "donut", "buns", "bun", "muffin", "cake", "scone"],
        "meat": ["meat", "chicken", "beef", "pork", "lamb", "sausage", "ham", "turkey", "bacon"],
        "drinks": ["drink", "drinks", "juice", "tea", "coffee", "soda", "smoothie", "latte", "kefir", "milkshake", "cider", "water", "beer", "wine"],
        "other": [],
    }

    SEASON_CHOICES = [
        ("seasonal", "Seasonal date range"),
        ("spring", "Spring"),
        ("summer", "Summer"),
        ("autumn", "Autumn"),
        ("winter", "Winter"),
        ("all_year", "All year"),
    ]
    MONTH_CHOICES = [
        (1, "January"),
        (2, "February"),
        (3, "March"),
        (4, "April"),
        (5, "May"),
        (6, "June"),
        (7, "July"),
        (8, "August"),
        (9, "September"),
        (10, "October"),
        (11, "November"),
        (12, "December"),
    ]
    SEASON_MONTHS = {
        "spring": (3, 5),
        "summer": (6, 8),
        "autumn": (9, 11),
        "winter": (12, 2),
    }

    name = models.CharField(max_length=255)

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
    )

    description = models.TextField(blank=True)
    unit_label = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="other")
    image_url = models.URLField(blank=True, default="")
    is_organic = models.BooleanField(default=False)
    food_miles = models.DecimalField(max_digits=6, decimal_places=1, blank=True, null=True)
    seasonal_availability = models.CharField(max_length=20, choices=SEASON_CHOICES, default="all_year")
    season_start_month = models.PositiveSmallIntegerField(choices=MONTH_CHOICES, blank=True, null=True)
    season_end_month = models.PositiveSmallIntegerField(choices=MONTH_CHOICES, blank=True, null=True)

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

    def _season_month_bounds(self):
        if self.seasonal_availability == "all_year":
            return None
        if self.season_start_month and self.season_end_month:
            return self.season_start_month, self.season_end_month
        return self.SEASON_MONTHS.get(self.seasonal_availability)

    @staticmethod
    def _month_in_range(month, start_month, end_month):
        if start_month <= end_month:
            return start_month <= month <= end_month
        return month >= start_month or month <= end_month

    @property
    def seasonal_range_display(self):
        bounds = self._season_month_bounds()
        if not bounds:
            return ""
        month_names = dict(self.MONTH_CHOICES)
        return f"Available: {month_names[bounds[0]]} - {month_names[bounds[1]]}"

    def is_currently_in_season(self, reference_date=None):
        if self.seasonal_availability == "all_year":
            return True

        reference_date = reference_date or timezone.localdate()
        bounds = self._season_month_bounds()
        if not bounds:
            return False
        return self._month_in_range(reference_date.month, bounds[0], bounds[1])

    @property
    def seasonal_status_label(self):
        if self.seasonal_availability == "all_year":
            return "Available year-round"
        return "In season" if self.is_currently_in_season() else "Out of season"

    @property
    def avoid_category_labels(self):
        text = " ".join(
            [self.allergens or "", self.name or "", self.description or ""]
        ).lower()
        labels = []

        for category_key, category_label in self.CATEGORY_CHOICES:
            matched = self.category == category_key
            for term in self.AVOID_CATEGORY_KEYWORDS.get(category_key, []):
                if term in text:
                    matched = True
                    break
            if matched:
                labels.append(category_label)

        return labels

    @property
    def can_be_ordered(self):
        return self.is_active and self.stock_quantity > 0 and self.is_currently_in_season()

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

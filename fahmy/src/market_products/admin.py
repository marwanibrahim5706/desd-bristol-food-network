from django.contrib import admin

from .models import FarmStory, FavouriteRecipe, Product, Recipe, Review


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "producer", "category", "price", "stock_quantity", "is_organic", "food_miles", "seasonal_availability", "is_active")
    list_filter = ("is_active", "category", "is_organic", "seasonal_availability")
    search_fields = ("name", "description", "allergens", "producer__username", "producer__email", "producer__business_name")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "moderation_status", "verified_purchase", "created_at")
    list_filter = ("rating", "moderation_status", "verified_purchase", "created_at")
    search_fields = ("product__name", "user__username", "comment", "producer_response")


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("title", "producer", "status", "moderation_status", "seasonal_tag", "published_at")
    list_filter = ("status", "moderation_status", "seasonal_tag")
    search_fields = ("title", "producer__username", "producer__business_name", "description")
    filter_horizontal = ("products",)


@admin.register(FarmStory)
class FarmStoryAdmin(admin.ModelAdmin):
    list_display = ("title", "producer", "status", "moderation_status", "seasonal_tag", "published_at")
    list_filter = ("status", "moderation_status", "seasonal_tag")
    search_fields = ("title", "producer__username", "producer__business_name", "summary", "body")


@admin.register(FavouriteRecipe)
class FavouriteRecipeAdmin(admin.ModelAdmin):
    list_display = ("customer", "recipe", "created_at")
    search_fields = ("customer__username", "recipe__title")

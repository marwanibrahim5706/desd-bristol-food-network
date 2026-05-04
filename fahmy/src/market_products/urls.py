from django.urls import path
from . import views

urlpatterns = [
    path("", views.discovery, name="discovery"),
    path("discover/", views.discovery, name="discovery_alt"),
    path("producers/", views.producer_directory, name="producer_directory"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/review/", views.submit_review, name="submit_review"),
    path("reviews/<int:review_id>/respond/", views.producer_respond_review, name="producer_respond_review"),
    path("recipes/<int:recipe_id>/", views.recipe_detail, name="recipe_detail"),
    path("recipes/<int:recipe_id>/favourite/", views.toggle_favourite_recipe, name="toggle_favourite_recipe"),
    path("stories/<int:story_id>/", views.story_detail, name="story_detail"),
    path("producers/<int:producer_id>/", views.producer_profile, name="producer_profile"),
    path("producer/content/", views.producer_content_dashboard, name="producer_content_dashboard"),
    path("producer/content/recipes/add/", views.producer_recipe_create, name="producer_recipe_create"),
    path("producer/content/recipes/<int:recipe_id>/edit/", views.producer_recipe_edit, name="producer_recipe_edit"),
    path("producer/content/stories/add/", views.producer_story_create, name="producer_story_create"),
    path("producer/content/stories/<int:story_id>/edit/", views.producer_story_edit, name="producer_story_edit"),
    path("producer/products/", views.producer_product_list, name="producer_product_list"),
    path("producer/products/add/", views.producer_product_create, name="producer_product_create"),
    path("producer/products/<int:pk>/edit/", views.producer_product_edit, name="producer_product_edit"),
    path("producer/products/<int:pk>/delete/", views.producer_product_delete, name="producer_product_delete"),
]

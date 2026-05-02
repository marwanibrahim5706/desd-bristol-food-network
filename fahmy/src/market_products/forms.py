from django import forms

from .models import FarmStory, Product, Recipe, Review


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "image_url",
            "is_organic",
            "food_miles",
            "seasonal_availability",
            "price",
            "stock_quantity",
            "low_stock_threshold",
            "is_active",
            "allergens",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "image_url": forms.URLInput(
                attrs={"placeholder": "https://example.com/product-image.jpg"}
            ),
            "allergens": forms.TextInput(
                attrs={"placeholder": "e.g. Nuts, Milk, Gluten"}
            ),
        }


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "comment", "is_anonymous"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Share what the product was like when it arrived."}),
        }


class ProducerReviewResponseForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["producer_response"]
        widgets = {
            "producer_response": forms.Textarea(attrs={"rows": 3, "placeholder": "Reply to this verified review."}),
        }


class RecipeForm(forms.ModelForm):
    products = forms.ModelMultipleChoiceField(
        queryset=Product.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Recipe
        fields = [
            "title",
            "description",
            "ingredients",
            "instructions",
            "storage_guidance",
            "freshness_guidance",
            "seasonal_tag",
            "image_url",
            "status",
            "products",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "ingredients": forms.Textarea(attrs={"rows": 6}),
            "instructions": forms.Textarea(attrs={"rows": 8}),
            "storage_guidance": forms.Textarea(attrs={"rows": 3}),
            "freshness_guidance": forms.Textarea(attrs={"rows": 3}),
            "image_url": forms.URLInput(
                attrs={
                    "placeholder": "https://example.com/recipe-image.jpg",
                }
            ),
        }

    def __init__(self, *args, producer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if producer is not None:
            self.fields["products"].queryset = Product.objects.filter(producer=producer).order_by("name")


class FarmStoryForm(forms.ModelForm):
    class Meta:
        model = FarmStory
        fields = [
            "title",
            "summary",
            "body",
            "educational_content",
            "seasonal_tag",
            "image_url",
            "status",
        ]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 3}),
            "body": forms.Textarea(attrs={"rows": 8}),
            "educational_content": forms.Textarea(attrs={"rows": 4}),
            "image_url": forms.URLInput(
                attrs={
                    "placeholder": "https://example.com/story-image.jpg",
                }
            ),
        }

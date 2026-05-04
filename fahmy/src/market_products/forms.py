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
            "season_start_month",
            "season_end_month",
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
        labels = {
            "seasonal_availability": "Availability pattern",
            "season_start_month": "Available from",
            "season_end_month": "Available until",
        }
        help_texts = {
            "seasonal_availability": "Use Available year-round for stored or permanent products. Use Seasonal date range for fruit and vegetables with specific months.",
            "season_start_month": "First month customers can order this seasonal product.",
            "season_end_month": "Last month customers can order this seasonal product.",
        }

    def clean(self):
        cleaned_data = super().clean()
        seasonal_availability = cleaned_data.get("seasonal_availability")
        start_month = cleaned_data.get("season_start_month")
        end_month = cleaned_data.get("season_end_month")

        if seasonal_availability == "seasonal" and (not start_month or not end_month):
            raise forms.ValidationError("Choose the start and end months for seasonal products.")

        if seasonal_availability == "all_year":
            cleaned_data["season_start_month"] = None
            cleaned_data["season_end_month"] = None

        return cleaned_data


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

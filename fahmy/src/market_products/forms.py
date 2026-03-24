from django import forms
from .models import Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "price",
            "stock_quantity",
            "low_stock_threshold",
            "is_active",
            "allergens",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "allergens": forms.TextInput(
                attrs={"placeholder": "e.g. Nuts, Milk, Gluten"}
            ),
        }
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_products", "0005_product_image_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="food_miles",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="is_organic",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="product",
            name="seasonal_availability",
            field=models.CharField(
                choices=[
                    ("spring", "Spring"),
                    ("summer", "Summer"),
                    ("autumn", "Autumn"),
                    ("winter", "Winter"),
                    ("all_year", "All year"),
                ],
                default="all_year",
                max_length=20,
            ),
        ),
    ]

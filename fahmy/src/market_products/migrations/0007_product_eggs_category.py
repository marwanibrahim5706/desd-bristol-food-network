from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_products", "0006_product_discovery_filters"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="category",
            field=models.CharField(
                choices=[
                    ("fruit_veg", "Fruit & Vegetables"),
                    ("dairy", "Dairy"),
                    ("eggs", "Eggs"),
                    ("bakery", "Bakery"),
                    ("meat", "Meat"),
                    ("drinks", "Drinks"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=50,
            ),
        ),
    ]

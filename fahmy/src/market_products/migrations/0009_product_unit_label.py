from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_products", "0008_product_season_months"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="unit_label",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]

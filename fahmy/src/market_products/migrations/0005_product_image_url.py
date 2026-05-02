from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_products", "0004_review_fields_recipe_farmstory_favouriterecipe"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="image_url",
            field=models.URLField(blank=True, default=""),
        ),
    ]

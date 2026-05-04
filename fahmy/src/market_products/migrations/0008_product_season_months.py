from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_products", "0007_product_eggs_category"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="seasonal_availability",
            field=models.CharField(
                choices=[
                    ("seasonal", "Seasonal date range"),
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
        migrations.AddField(
            model_name="product",
            name="season_start_month",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
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
                ],
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="season_end_month",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
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
                ],
                null=True,
            ),
        ),
    ]

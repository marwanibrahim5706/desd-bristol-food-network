import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_orders", "0002_order_customer_phone_order_delivery_address_and_more"),
        ("market_products", "0007_product_eggs_category"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_items",
                to="market_products.product",
            ),
        ),
    ]

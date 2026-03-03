from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Product


def discovery(request):
    q = (request.GET.get("q") or "").strip()
    available = request.GET.get("available")  # "1" => available only

    qs = Product.objects.select_related("producer").all()

    # default show active only
    qs = qs.filter(is_active=True)

    # optional filter: only stock > 0
    if available in ("1", "true", "True", "yes", "on"):
        qs = qs.filter(stock_quantity__gt=0)

    # search by product name OR producer username/email
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(producer__username__icontains=q)
            | Q(producer__email__icontains=q)
        )

    qs = qs.order_by("-id")

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "market_products/discovery.html",
        {"page_obj": page_obj, "q": q, "available": available},
    )


def product_detail(request, pk: int):
    product = get_object_or_404(
        Product.objects.select_related("producer"),
        pk=pk,
        is_active=True,
    )
    return render(request, "market_products/product_detail.html", {"product": product})
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from market_products.models import FarmStory, FavouriteRecipe, Product, Recipe, Review


class Command(BaseCommand):
    help = "Seed farm stories, recipes, favourites, and verified reviews."

    def handle(self, *args, **options):
        now = timezone.now()
        User = get_user_model()

        producers = {user.username: user for user in User.objects.filter(role=User.Role.PRODUCER)}
        customers = {user.username: user for user in User.objects.filter(role=User.Role.CUSTOMER)}
        review_accounts = {}
        for username in ["reviewer_apples", "reviewer_potatoes", "reviewer_milk", "reviewer_eggs", "reviewer_loaf", "reviewer_donuts"]:
            review_accounts[username], _ = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.test",
                    "role": User.Role.CUSTOMER,
                    "first_name": "Verified customer",
                },
            )
        products = {product.name: product for product in Product.objects.select_related("producer")}

        recipe_specs = [
            {
                "producer": "producer1",
                "title": "Roasted Orchard Tray Bake",
                "description": "A simple tray bake built around seasonal produce from Green Farm Co.",
                "ingredients": (
                    "1 box apples\n"
                    "1 bag carrots\n"
                    "1 sack potatoes\n"
                    "2 tbsp rapeseed oil\n"
                    "Sea salt\n"
                    "Black pepper\n"
                    "Fresh thyme"
                ),
                "instructions": (
                    "1. Heat the oven to 200C.\n"
                    "2. Cut the apples, carrots, and potatoes into bite-sized pieces.\n"
                    "3. Toss with rapeseed oil, salt, pepper, and thyme.\n"
                    "4. Roast for 35 to 40 minutes until caramelised.\n"
                    "5. Serve warm as a hearty side or light supper."
                ),
                "storage_guidance": "Keep leftovers chilled and eat within 2 days. Reheat in the oven for best texture.",
                "freshness_guidance": "Use the apples within a week and keep potatoes in a cool dark place.",
                "seasonal_tag": Recipe.Season.AUTUMN,
                "image_url": "https://images.unsplash.com/photo-1518977676601-b53f82aba655?auto=format&fit=crop&w=1200&q=80",
                "products": ["Apples Box", "Carrots Bag", "Potatoes Sack"],
            },
            {
                "producer": "producer1",
                "title": "Tomato and Courgette Market Salad",
                "legacy_titles": ["Tomato and Olive Oil Market Salad"],
                "description": "A bright salad that keeps the tomatoes front and centre.",
                "ingredients": (
                    "1 tomato crate\n"
                    "1 courgette box\n"
                    "3 tbsp dressing\n"
                    "Pinch of salt\n"
                    "Cracked black pepper\n"
                    "Fresh herbs"
                ),
                "instructions": (
                    "1. Slice the tomatoes generously.\n"
                    "2. Arrange on a platter and season.\n"
                    "3. Shave courgettes into ribbons and spoon over dressing and herbs.\n"
                    "4. Rest for 10 minutes before serving."
                ),
                "storage_guidance": "Best eaten fresh on the day it is prepared.",
                "freshness_guidance": "Store tomatoes at room temperature for the fullest flavour.",
                "seasonal_tag": Recipe.Season.SUMMER,
                "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Tomato%20salad.jpg",
                "products": ["Tomatoes Crate", "Courgettes Box"],
            },
            {
                "producer": "producer1",
                "title": "Weekend Farmhouse Toast Board",
                "description": "An easy sharing board using bread, apples, and pantry staples from the producer.",
                "ingredients": (
                    "1 fresh bread loaf\n"
                    "1 box apples\n"
                    "Butter\n"
                    "Cinnamon\n"
                    "Honey or jam"
                ),
                "instructions": (
                    "1. Slice and toast the bread.\n"
                    "2. Pan-soften the apples with cinnamon.\n"
                    "3. Spread the toast lightly with butter.\n"
                    "4. Top with warm apples and serve family-style."
                ),
                "storage_guidance": "Bread is best within 2 days; freeze extra slices if needed.",
                "freshness_guidance": "Keep the loaf wrapped at room temperature and apples cool and dry.",
                "seasonal_tag": Recipe.Season.ALL_YEAR,
                "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Bread%20loaf.jpg",
                "products": ["Fresh Bread", "Apples Box"],
            },
            {
                "producer": "producer1",
                "title": "Green Farm Breakfast Tray",
                "legacy_titles": ["Creamy Breakfast Brunch Tray"],
                "description": "A hearty brunch tray built around Green Farm Co eggs, potatoes, and tomatoes.",
                "ingredients": (
                    "4 eggs\n"
                    "2 potatoes\n"
                    "2 tomatoes\n"
                    "2 tbsp rapeseed oil\n"
                    "Salt\n"
                    "Pepper\n"
                    "Chopped herbs"
                ),
                "instructions": (
                    "1. Roast sliced potatoes in rapeseed oil until nearly tender.\n"
                    "2. Add tomatoes to the tray and season well.\n"
                    "3. Crack in the eggs and return to the oven until just set.\n"
                    "4. Finish with herbs and serve straight from the tray."
                ),
                "storage_guidance": "Cooked eggs are best eaten fresh. Keep any leftovers chilled and eat within 1 day.",
                "freshness_guidance": "Store eggs cool, keep potatoes in a dark place, and leave tomatoes at room temperature until ripe.",
                "seasonal_tag": Recipe.Season.ALL_YEAR,
                "image_url": "https://images.unsplash.com/photo-1482049016688-2d3e1b311543?auto=format&fit=crop&w=1200&q=80",
                "products": ["Eggs Tray", "Potatoes Sack", "Tomatoes Crate"],
            },
            {
                "producer": "producer2",
                "title": "Cheese and Yogurt Savoury Dip Board",
                "description": "A simple snack board for cafes and office lunches.",
                "ingredients": (
                    "1 cheese pack\n"
                    "1 yogurt pack\n"
                    "Olive oil\n"
                    "Lemon zest\n"
                    "Black pepper"
                ),
                "instructions": (
                    "1. Stir yogurt until smooth.\n"
                    "2. Fold in lemon zest and pepper.\n"
                    "3. Crumble or shave over the cheese.\n"
                    "4. Serve with bread or crackers."
                ),
                "storage_guidance": "Keep chilled and use within 24 hours once mixed.",
                "freshness_guidance": "Open yogurt close to serving time for the freshest result.",
                "seasonal_tag": Recipe.Season.ALL_YEAR,
                "image_url": "https://images.unsplash.com/photo-1452195100486-9cc805987862?auto=format&fit=crop&w=1200&q=80",
                "products": ["Cheese 500g", "Yogurt Pack"],
            },
            {
                "producer": "producer2",
                "title": "Golden Cheese Melt Toastie",
                "description": "Comfort food built from the dairy house range.",
                "ingredients": "Cheese slices\nButter\nFresh bread\nOptional mustard",
                "instructions": (
                    "1. Butter the outside of the bread.\n"
                    "2. Fill generously with cheese.\n"
                    "3. Toast in a pan until golden.\n"
                    "4. Rest briefly before slicing."
                ),
                "storage_guidance": "Assemble fresh for best results.",
                "freshness_guidance": "Bring the cheese out of the fridge 10 minutes before cooking for a smoother melt.",
                "seasonal_tag": Recipe.Season.WINTER,
                "image_url": "https://images.unsplash.com/photo-1528735602780-2552fd46c7af?auto=format&fit=crop&w=1200&q=80",
                "products": ["Cheese 500g"],
            },
            {
                "producer": "producer3",
                "title": "Bakery Breakfast Basket",
                "description": "A warm bakery spread featuring loaves, brioche, and pastries from Bakers Corner.",
                "ingredients": "1 wholemeal loaf\n1 brioche\n1 croissant box\nButter or jam\nFresh fruit",
                "instructions": (
                    "1. Warm the pastries briefly.\n"
                    "2. Slice the loaf and brioche.\n"
                    "3. Arrange everything on a serving board.\n"
                    "4. Serve with spreads and fruit."
                ),
                "storage_guidance": "Pastries are best on the day of purchase; freeze extra bread.",
                "freshness_guidance": "Refresh the croissants in a low oven for 4 minutes before serving.",
                "seasonal_tag": Recipe.Season.ALL_YEAR,
                "image_url": "https://images.unsplash.com/photo-1509440159596-0249088772ff?auto=format&fit=crop&w=1200&q=80",
                "products": ["Wholemeal Loaf", "Brioche", "Croissant Box"],
            },
            {
                "producer": "producer3",
                "title": "Market Donut Dessert Platter",
                "description": "An easy dessert option for events and shared tables.",
                "ingredients": "1 donut pack\nSeasonal berries\nChocolate drizzle\nIcing sugar",
                "instructions": (
                    "1. Arrange donuts on a platter.\n"
                    "2. Add berries around the edges.\n"
                    "3. Finish with chocolate drizzle and icing sugar.\n"
                    "4. Serve immediately."
                ),
                "storage_guidance": "Best enjoyed on the day of collection.",
                "freshness_guidance": "Keep donuts covered at room temperature and avoid refrigeration.",
                "seasonal_tag": Recipe.Season.SUMMER,
                "image_url": "https://images.unsplash.com/photo-1551024601-bec78aea704b?auto=format&fit=crop&w=1200&q=80",
                "products": ["Donut Pack"],
            },
            {
                "producer": "producer3",
                "title": "Bakery Toast and Soup Lunch",
                "description": "A wholesome lunch pairing the bakery breads with soup or salad.",
                "ingredients": "1 fresh bread loaf or wholemeal loaf\nButter\nSoup of choice",
                "instructions": (
                    "1. Slice the loaf thickly.\n"
                    "2. Toast until crisp at the edges.\n"
                    "3. Spread with butter and serve with soup."
                ),
                "storage_guidance": "Use the bread within 2 days or freeze.",
                "freshness_guidance": "Store in a bread bin or paper bag to preserve crust texture.",
                "seasonal_tag": Recipe.Season.WINTER,
                "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Soup%20with%20buttered%20bread.jpg",
                "products": ["Fresh Bread", "Wholemeal Loaf"],
            },
        ]

        story_specs = [
            {
                "producer": "producer1",
                "title": "How We Pack the Week's Harvest",
                "summary": "A look at how Green Farm Co prepares apples, carrots, potatoes, and tomatoes for the weekly market.",
                "body": (
                    "Every market week starts before sunrise. We check field moisture, pick only what is ready, and grade "
                    "produce by hand so customers receive reliable quality. Our apples are boxed the same morning they are "
                    "picked, carrots are brushed rather than over-washed to protect shelf life, and potatoes are cured before "
                    "bagging to reduce bruising. This slower handling means produce arrives looking honest and cooking well "
                    "for the week ahead."
                ),
                "educational_content": (
                    "Store apples cool, carrots sealed, and potatoes in a dark ventilated cupboard. Tomatoes should stay "
                    "at room temperature for the best flavour."
                ),
                "seasonal_tag": FarmStory.Season.AUTUMN,
                "image_url": "https://images.unsplash.com/photo-1464226184884-fa280b87c399?auto=format&fit=crop&w=1200&q=80",
            },
            {
                "producer": "producer1",
                "title": "Why Seasonal Tomatoes Taste Better",
                "summary": "Why local tomatoes change through the season and how that affects cooking.",
                "body": (
                    "Tomatoes develop sweetness and acidity differently as the summer progresses. Early season tomatoes tend "
                    "to be brighter and firmer, while peak-summer fruit carries more natural sugars and a softer bite. We "
                    "encourage customers to use the first harvest in salads and save the ripest crates for sauces, roasting, "
                    "and slow cooking."
                ),
                "educational_content": "Avoid refrigeration where possible. Cold storage dulls tomato aroma and texture.",
                "seasonal_tag": FarmStory.Season.SUMMER,
                "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Tomato%20salad.jpg",
            },
            {
                "producer": "producer2",
                "title": "From Dairy Churn to Market Shelf",
                "summary": "Fresh Dairy House shares how milk, butter, yogurt, and cheese are prepared for local delivery.",
                "body": (
                    "Our dairy days are scheduled tightly so chilled goods stay consistently cold from production to collection. "
                    "Milk is bottled in small batches, butter is portioned while still fresh, and cultured products like yogurt "
                    "and cheese are checked for texture before dispatch. The goal is a short, traceable chain that keeps "
                    "flavour and food safety equally strong."
                ),
                "educational_content": "Always return dairy to the fridge promptly. Soft dairy products are best stored below 5C.",
                "seasonal_tag": FarmStory.Season.ALL_YEAR,
                "image_url": "https://images.unsplash.com/photo-1563636619-e9143da7973b?auto=format&fit=crop&w=1200&q=80",
            },
            {
                "producer": "producer2",
                "title": "Eggs, Butter, and Simple Cooking",
                "summary": "A producer guide to getting more from staple breakfast ingredients.",
                "body": (
                    "Some of the best meals start with a few dependable basics. Eggs, milk, butter, and cheese can become "
                    "breakfast trays, quick bakes, or easy lunch dishes without much effort. We work with customers who want "
                    "reliable staples for households, cafes, and community kitchens, so consistency matters just as much as "
                    "richness and flavour."
                ),
                "educational_content": "Use eggs at room temperature for baking, and keep butter wrapped so it does not absorb fridge odours.",
                "seasonal_tag": FarmStory.Season.WINTER,
                "image_url": "https://images.unsplash.com/photo-1518569656558-1f25e69d93d7?auto=format&fit=crop&w=1200&q=80",
            },
            {
                "producer": "producer3",
                "title": "Baking Through the Night for Morning Collections",
                "summary": "Bakers Corner explains the rhythm behind bread, brioche, croissants, and donuts.",
                "body": (
                    "The bakery is at its busiest when most people are asleep. Doughs are mixed and rested overnight so that "
                    "loaves have time to develop flavour naturally. Pastries are shaped before dawn, and sweet items like "
                    "donuts are finished closest to collection time. That timing helps us put crisp crusts and soft centres "
                    "into customers' hands at their best."
                ),
                "educational_content": "Warm bread briefly in the oven to refresh it, and keep pastries covered but not airtight to protect texture.",
                "seasonal_tag": FarmStory.Season.ALL_YEAR,
                "image_url": "https://images.unsplash.com/photo-1517433670267-08bbd4be890f?auto=format&fit=crop&w=1200&q=80",
            },
            {
                "producer": "producer3",
                "title": "Choosing the Right Bread for the Meal",
                "summary": "A simple guide to pairing wholemeal loaves, fresh bread, brioche, and pastries with different meals.",
                "body": (
                    "Not every loaf suits the same table. Wholemeal bread stands up well to soups and savoury toppings, while "
                    "softer white loaves make everyday sandwiches and toast. Brioche works beautifully with breakfast spreads "
                    "and desserts, and croissants shine when served warm with dairy or fruit. Helping customers choose the "
                    "right bake is part of helping them waste less food."
                ),
                "educational_content": "Slice and freeze surplus bread on the first day, then toast from frozen as needed.",
                "seasonal_tag": FarmStory.Season.SPRING,
                "image_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Bread%20loaf.jpg",
            },
        ]

        review_specs = [
            {
                "username": "reviewer_apples",
                "product": "Apples Box",
                "rating": 5,
                "comment": "Crisp, sweet, and packed really well. The apples lasted all week and tasted genuinely fresh.",
                "is_anonymous": False,
                "producer_response": "Thank you for the lovely feedback. We picked that batch the morning before market and are glad it kept well for you.",
            },
            {
                "username": "reviewer_potatoes",
                "product": "Potatoes Sack",
                "rating": 4,
                "comment": "Great roasting potatoes with good texture. A couple were smaller than expected but overall very good quality.",
                "is_anonymous": True,
                "producer_response": "Appreciate the helpful note. We sort by weight range, and we will keep an even closer eye on the next batch.",
            },
            {
                "username": "reviewer_milk",
                "product": "Milk 1L",
                "rating": 5,
                "comment": "Very fresh and noticeably creamier than supermarket milk. Worked perfectly for breakfasts and cooking.",
                "is_anonymous": False,
                "producer_response": "That is wonderful to hear. We aim to keep the turnaround from bottling to delivery as short as possible.",
            },
            {
                "username": "reviewer_eggs",
                "product": "Eggs Tray",
                "rating": 5,
                "comment": "Reliable quality and excellent for brunch service. Clean shells and rich yolks.",
                "is_anonymous": False,
                "producer_response": "Thank you. We know consistency matters, especially when customers are cooking for groups.",
            },
            {
                "username": "reviewer_loaf",
                "product": "Wholemeal Loaf",
                "rating": 5,
                "comment": "Excellent loaf with a great crust and soft middle. Toasted beautifully the next morning.",
                "is_anonymous": False,
                "producer_response": "We are really pleased it held up well the next day. That loaf is one of our favourites for toast too.",
            },
            {
                "username": "reviewer_donuts",
                "product": "Donut Pack",
                "rating": 4,
                "comment": "Soft and fresh with a good balance of sweetness. Best on the day, but that is exactly what we wanted.",
                "is_anonymous": True,
                "producer_response": "Thanks for the kind review. We agree they are best enjoyed fresh on the collection day.",
            },
        ]

        created_counts = {"recipes": 0, "stories": 0, "reviews": 0, "favourites": 0}

        Recipe.objects.filter(title="Tomato and Olive Oil Market Salad").delete()

        for spec in recipe_specs:
            recipe = Recipe.objects.filter(
                title__in=[spec["title"], *spec.get("legacy_titles", [])]
            ).first()
            created = recipe is None
            if created:
                recipe = Recipe.objects.create(
                    producer=producers[spec["producer"]],
                    title=spec["title"],
                    description=spec["description"],
                    ingredients=spec["ingredients"],
                    instructions=spec["instructions"],
                    storage_guidance=spec["storage_guidance"],
                    freshness_guidance=spec["freshness_guidance"],
                    seasonal_tag=spec["seasonal_tag"],
                    image_url=spec["image_url"],
                    status=Recipe.Status.PUBLISHED,
                    moderation_status=Recipe.ModerationStatus.APPROVED,
                    published_at=now,
                )
                created_counts["recipes"] += 1
            else:
                recipe.producer = producers[spec["producer"]]
                recipe.title = spec["title"]
                recipe.description = spec["description"]
                recipe.ingredients = spec["ingredients"]
                recipe.instructions = spec["instructions"]
                recipe.storage_guidance = spec["storage_guidance"]
                recipe.freshness_guidance = spec["freshness_guidance"]
                recipe.seasonal_tag = spec["seasonal_tag"]
                recipe.image_url = spec["image_url"]
                recipe.status = Recipe.Status.PUBLISHED
                recipe.moderation_status = Recipe.ModerationStatus.APPROVED
                recipe.published_at = recipe.published_at or now
                recipe.save()
            recipe.products.set([products[name] for name in spec["products"]])

        for spec in story_specs:
            story, created = FarmStory.objects.get_or_create(
                producer=producers[spec["producer"]],
                title=spec["title"],
                defaults={
                    "summary": spec["summary"],
                    "body": spec["body"],
                    "educational_content": spec["educational_content"],
                    "seasonal_tag": spec["seasonal_tag"],
                    "image_url": spec["image_url"],
                    "status": FarmStory.Status.PUBLISHED,
                    "moderation_status": FarmStory.ModerationStatus.APPROVED,
                    "published_at": now,
                },
            )
            if created:
                created_counts["stories"] += 1
            else:
                story.summary = spec["summary"]
                story.body = spec["body"]
                story.educational_content = spec["educational_content"]
                story.seasonal_tag = spec["seasonal_tag"]
                story.image_url = spec["image_url"]
                story.status = FarmStory.Status.PUBLISHED
                story.moderation_status = FarmStory.ModerationStatus.APPROVED
                story.published_at = story.published_at or now
                story.save()

        for spec in review_specs:
            review, created = Review.objects.get_or_create(
                user=review_accounts[spec["username"]],
                product=products[spec["product"]],
                defaults={
                    "rating": spec["rating"],
                    "comment": spec["comment"],
                    "is_anonymous": spec["is_anonymous"],
                    "verified_purchase": True,
                    "moderation_status": Review.ModerationStatus.PUBLISHED,
                    "producer_response": spec["producer_response"],
                    "producer_responded_at": now,
                },
            )
            if created:
                created_counts["reviews"] += 1
            else:
                review.rating = spec["rating"]
                review.comment = spec["comment"]
                review.is_anonymous = spec["is_anonymous"]
                review.verified_purchase = True
                review.moderation_status = Review.ModerationStatus.PUBLISHED
                review.producer_response = spec["producer_response"]
                review.producer_responded_at = now
                review.save()

        favourite_pairs = [
            ("customer1", "Roasted Orchard Tray Bake"),
            ("customer2", "Green Farm Breakfast Tray"),
            ("customer3", "Bakery Breakfast Basket"),
        ]
        for username, recipe_title in favourite_pairs:
            _, created = FavouriteRecipe.objects.get_or_create(
                customer=customers[username],
                recipe=Recipe.objects.get(title=recipe_title),
            )
            if created:
                created_counts["favourites"] += 1

        self.stdout.write(self.style.SUCCESS(f"Seed complete: {created_counts}"))
        self.stdout.write(
            self.style.SUCCESS(
                "Totals now -> "
                f"recipes: {Recipe.objects.count()}, "
                f"stories: {FarmStory.objects.count()}, "
                f"reviews: {Review.objects.count()}, "
                f"favourites: {FavouriteRecipe.objects.count()}"
            )
        )

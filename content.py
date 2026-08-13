"""Site copy, kept out of the templates.

Wording carried over from the original recipes-heroku home page, with the
typos fixed ("their are" -> "there are", "worthly" -> "worthy",
"accompanyment" -> "accompaniment", "homeade" -> "homemade").
"""

SITE = {
    "name": "Recipes",
    "owner": "Dustin Cremascoli",
    "tagline": "by Dustin Cremascoli and Family",
    "title": "Recipes — Dustin Cremascoli and Family",
    "description": (
        "A free and full repository of our family's favorite and best recipes. "
        "Browse, print or contribute your own."
    ),
    "url": "https://recipes.dustincremascoli.com",
    "main_site": "https://www.dustincremascoli.com",
    "socials": [
        {"label": "GitHub", "href": "https://github.com/dcremas", "icon": "github"},
        {
            "label": "LinkedIn",
            "href": "https://www.linkedin.com/in/dustin-cremascoli-662105423/",
            "icon": "linkedin",
        },
    ],
}

HERO = {
    "greeting": "Hi, I'm Dustin Cremascoli — and this is my recipe site.",
    "lede": "A free and full repository of our family's favorite and best recipes.",
    "body": (
        "Feel free to browse, print and use any recipe you see here. If you have "
        "a good recipe to contribute, create an account and post it for the "
        "collection. This is meant to be a free spot to discover, use and share "
        "our favorites."
    ),
}

FEATURES = [
    {
        "icon": "book",
        "title": "Browse the recipes",
        "body": "Take a look around and see whether there are any recipes that interest you.",
    },
    {
        "icon": "printer",
        "title": "Take a recipe",
        "body": (
            "You don't need an account to view a recipe or print it — every recipe "
            "page has a print-friendly layout built in."
        ),
    },
    {
        "icon": "share",
        "title": "Share a recipe",
        "body": "If you have a favorite recipe that's worthy, create an account and share it with the family.",
    },
    {
        "icon": "chat",
        "title": "Give feedback",
        "body": "Anything that could be improved — this is a work in progress, so feel free to say so.",
    },
]

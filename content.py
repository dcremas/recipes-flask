"""Site copy, kept out of the templates.

Wording carried over from the original recipes-heroku home page, with the
typos fixed ("their are" -> "there are", "worthly" -> "worthy",
"accompanyment" -> "accompaniment", "homeade" -> "homemade").

Every invitation to sign up and contribute has since been removed. The site is
read-and-print for visitors and authored only by the admin, so copy promising
otherwise was pointing at a door that is no longer there.
"""

SITE = {
    "name": "Recipes",
    "owner": "Dustin Cremascoli",
    "tagline": "by Dustin Cremascoli and Family",
    "title": "Recipes — Dustin Cremascoli and Family",
    "description": (
        "A free and full repository of our family's favorite and best recipes. "
        "Browse any of them, and print the ones you want to cook."
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
        "Feel free to browse, print and use any recipe you see here. Nothing is "
        "gated and nothing needs an account — this is meant to be a free spot to "
        "discover and use our favorites."
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
            "Every recipe page produces a clean one-page PDF, ready to print or "
            "save and take into the kitchen."
        ),
    },
    {
        "icon": "list",
        "title": "Find it fast",
        "body": "Filter by category, or switch to the table view to size up the whole collection at once.",
    },
    {
        "icon": "chat",
        "title": "Give feedback",
        "body": "Anything that could be improved — this is a work in progress, so feel free to say so.",
    },
]

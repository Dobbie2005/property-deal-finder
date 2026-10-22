# Property Deal Finder

Finds property listings that look **under market value** in:

- **Durbanville & surrounds** (20 km radius: Bellville, Brackenfell, Kraaifontein, Kuils River, Parow, Plattekloof…)
- **Polokwane** (25 km radius)
- **Tzaneen** (25 km radius)

How it works:

1. Property24 and Private Property email new listings to a **dedicated Gmail inbox**.
2. Every morning a **GitHub Action** reads those emails and saves each listing to a free **Supabase** database.
3. Each listing is compared with similar listings in the same suburb. A score like **"−18%"** means it's asking 18% less than similar places.
4. **`docs/index.html`** shows everything on a map, with pins coloured by how good the deal is.

Everything runs on free tiers. The only paid option is GitHub Pages for a private repo (see step 6).

---

## One-time setup (about 30 minutes)

### 1. Create the bot Gmail account
1. Create a new Gmail account, e.g. `ourflips.listings@gmail.com`.
2. Turn on **2-Step Verification**: Google Account → Security → 2-Step Verification.
3. Create an **app password**: go to <https://myaccount.google.com/apppasswords>, name it `deal-finder` and copy the 16-letter code. You don't need to turn on IMAP; it's always on now.

### 2. Set up listing alerts, sent to the bot Gmail
On **Property24** and **Private Property**, sign up with the bot Gmail, then save searches with email alerts turned on (daily or instant):

| Search | Suggested suburbs / areas |
|---|---|
| Northern suburbs, Cape Town | Durbanville, Bellville, Brackenfell, Kraaifontein, Kuils River, Parow, Goodwood, Edgemead, Bothasig |
| Polokwane | Polokwane (all suburbs) |
| Tzaneen | Tzaneen (all suburbs) |

Set your price ceiling and "for sale". **Don't filter too tightly.** The scoring needs plenty of "normal" listings to compare against, so include all houses, townhouses and apartments in those areas.

Any other listing email works too: forward a Property24 or Private Property link to the bot inbox and it gets picked up.

### 3. Get the Supabase secret key
The database already exists: Supabase project **property-deal-finder**.
Open it in the Supabase dashboard → **Project Settings → API Keys** → copy the **secret** key (starts with `sb_secret_`). If you only see legacy keys, copy the `service_role` key instead.
⚠️ Never put this key in a file. It only goes into GitHub secrets.

### 4. Put the code on GitHub
Create an empty repo called `property-deal-finder` and upload these files.

### 5. Add the secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `GMAIL_ADDRESS` | the bot Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-letter app password |
| `SUPABASE_SECRET_KEY` | the `sb_secret_…` key |
| `NOTIFY_EMAIL` *(optional)* | your own email. You'll get a message when a new listing is 10%+ below similar ones |

Then go to **Actions → Daily listing check → Run workflow** to test it. The run's summary page shows how many emails and listings it found, plus the top 10 deals. After that it runs every day at 05:17.

### 6. Open the map
- **On your computer:** download `docs/index.html` and double-click it.
- **As a link you can open on your phone:** GitHub Pages is free for **public** repos. Go to Settings → Pages → *Deploy from a branch* → `main` / `docs`. The map lives at `https://<your-username>.github.io/property-deal-finder/`. For a private repo, Pages needs a paid GitHub plan.

The key inside `index.html` is the **publishable** key. It can only *read* listings, so it's safe if the repo is public.

---

## Adding Facebook Marketplace (or any other) listings by hand
Add a row to `data/manual_listings.csv`. You can edit it right on GitHub with the pencil icon:

```
url,price,property_type,bedrooms,bathrooms,floor_m2,erf_m2,suburb,city,province,title
https://www.facebook.com/marketplace/item/123456,950000,house,3,1,110,,Kraaifontein,Cape Town,Western Cape,Fixer-upper on corner
```

It shows up on the map after the next run.

## How the score works
For each listing, the job looks at other listings **in the same suburb**, of the same kind (houses and townhouses together, apartments, land), seen in the **last 6 months**:

1. If the listing has a floor size and there are 3+ similar listings with sizes: *estimate = median R/m² × floor size*
2. Otherwise, with 3+ listings with the same number of bedrooms: *estimate = their median price*
3. Otherwise there's no score yet (grey pin)

`deal_score = (estimate − asking price) ÷ estimate`

**Read the scores with care.** They compare *asking* prices, not *sold* prices. They get better as more listings pile up, so give it a few weeks. And a cheap listing is often cheap for a reason. Always check condition and the street, and get recent sold prices from an agent before making an offer.

## Settings
`dealfinder/config.py` holds the regions (centre and radius), the minimum number of comparisons, the alert threshold and more.

## Running it on your own computer
```
pip install -r requirements.txt
python -m pytest tests                 # run the tests
python run.py --eml tests/sample_emails  # parse sample emails without touching the database
```

## Staying free
- **Supabase free plan**: no card on file, so you can't be billed. If you ever hit a limit (500 MB is far more than this needs), the project gets restricted, not charged. Free projects pause after 7 days of no activity; the daily job keeps it active.
- **GitHub Actions**: one short run a day fits well within the free minutes.
- GitHub turns off scheduled jobs in **public** repos after 60 days with no commits. If that happens, click "Enable workflow" on the Actions tab.

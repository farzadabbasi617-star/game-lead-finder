# Game Lead Finder 🎮

MVP قانونی برای ساخت لیست لیدهای حوزه گیم: فروشگاه بازی، فروشگاه کنسول، گیم‌نت، گیفت‌کارت، CP/UC/Gem، پیج/کانال عمومی و وب‌سایت‌های مرتبط.

این پروژه برای **Neon PostgreSQL + Render** آماده شده و یک پنل ساده دارد:

- ذخیره لیدها در دیتابیس
- حذف تکراری بر اساس URL
- امتیازدهی خودکار به لیدها
- دسته‌بندی ساده فارسی/انگلیسی
- تغییر وضعیت دستی: `new`, `checked`, `messaged`, `replied`, `registered`, `irrelevant`
- خروجی CSV مناسب Excel فارسی
- خروجی Excel `.xlsx`
- Import از CSV
- Enrichment روی وب‌سایت‌های عمومی برای پیدا کردن لینک Instagram/Telegram
- گزینه رایگان `Search Links` برای ساخت لینک‌های جستجوی دستی بدون API Key
- دکمه کپی متن پیام دعوت برای هر لید
- کالکتورهای API-based/قانونی:
  - Google Places API برای Google Maps
  - Neshan Search API برای نشان
  - SerpAPI برای سرچ وب، تلگرام عمومی، اینستاگرام عمومی، بلد، دیوار، شیپور، ترب از طریق نتایج عمومی گوگل
  - Google CSE، Brave Search، Serper، SearchAPI و Tavily به عنوان جایگزین‌های SerpAPI

> نکته: پروژه عمداً وارد لاگین، کپچا، پیام خودکار، استخراج اطلاعات خصوصی یا دور زدن محدودیت پلتفرم‌ها نمی‌شود.

---

## 1) اجرای محلی

```bash
cd game-lead-finder
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

بعد برو به:

```text
http://127.0.0.1:8000
```

در حالت لوکال اگر `DATABASE_URL` را تنظیم نکنی، از SQLite استفاده می‌شود.

---

## 2) اتصال به Neon

در Neon یک دیتابیس بساز و connection string را به شکل SQLAlchemy در `.env` یا Render بگذار:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST.neon.tech/DBNAME?sslmode=require
```

اگر Neon connection string به شکل معمولی `postgresql://...` داد، کافی است ابتدای آن را به این شکل تغییر بدهی:

```text
postgresql+psycopg://...
```

جدول‌ها در اولین اجرای اپ به صورت خودکار ساخته می‌شوند.

---

## 3) Deploy روی Render

1. پروژه را روی GitHub ببر.
2. در Render یک Web Service بساز.
3. Build command:

```bash
pip install -r requirements.txt
```

4. Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

5. Environment Variables را ست کن:

```env
DATABASE_URL=postgresql+psycopg://...
ADMIN_TOKEN=یک-توکن-سخت-و-خصوصی
PYTHON_VERSION=3.12.4
GOOGLE_PLACES_API_KEY=اختیاری
NESHAN_API_KEY=اختیاری
SERPAPI_KEY=اختیاری
GOOGLE_CSE_API_KEY=اختیاری
GOOGLE_CSE_ID=اختیاری
BRAVE_SEARCH_API_KEY=اختیاری
SERPER_API_KEY=اختیاری
SEARCHAPI_KEY=اختیاری
TAVILY_API_KEY=اختیاری
```

فایل `render.yaml` هم داخل پروژه هست، اگر خواستی از Blueprint استفاده کنی.

---

## 4) کلیدهای API

برای اینکه کالکتورها واقعاً داده بیاورند، حداقل یکی از این‌ها را لازم داری:

### Google Places API
برای سرچ روی Google Maps با روش رسمی.

متغیر:

```env
GOOGLE_PLACES_API_KEY=...
```

### Neshan API
برای سرچ رسمی نشان.

متغیر:

```env
NESHAN_API_KEY=...
```

Endpoint استفاده‌شده:

```text
https://api.neshan.org/v1/search?term=...&lat=...&lng=...
```

### SerpAPI یا جایگزین‌ها
برای سرچ وب عمومی و پیدا کردن لینک‌های:

- `t.me/...`
- `instagram.com/...`
- `balad.ir/...`
- `divar.ir/...`
- `sheypoor.com/...`
- `torob.com/...`
- سایت‌های مستقل

متغیرهای قابل استفاده؛ فقط یکی از این سرویس‌ها کافی است:

```env
SERPAPI_KEY=...

# Google Programmable Search Engine
GOOGLE_CSE_API_KEY=...
GOOGLE_CSE_ID=...

# Brave Search
BRAVE_SEARCH_API_KEY=...

# Serper.dev
SERPER_API_KEY=...

# SearchAPI.io
SEARCHAPI_KEY=...

# Tavily
TAVILY_API_KEY=...
```

اگر هیچ API Key ست نکنی، پنل و دیتابیس کار می‌کنند. گزینه `Search Links رایگان` هم بدون API Key لینک‌های جستجوی دستی می‌سازد؛ اما برای جمع‌آوری لید واقعی و خودکار، حداقل یکی از API keyها لازم است.

---

## 5) استفاده از پنل

در صفحه اصلی:

1. `ADMIN_TOKEN` را وارد کن.
2. منبع را انتخاب کن: همه، Google Places، Neshan، یا Web/SerpAPI.
3. تعداد کلمه، شهر و نتیجه را کم نگه دار برای تست اول.
4. روی «شروع جمع‌آوری» بزن.
5. لیدها را بررسی کن و status را تغییر بده.
6. خروجی CSV بگیر.

پیشنهاد تست اول:

```text
keyword_limit = 5
city_limit = 3
result_limit = 8
```

اگر کیفیت خوب بود، کم‌کم زیادش کن.

---

## 6) اجرای زمان‌بندی‌شده

می‌توانی با cron-job.org یا GitHub Actions این endpoint را صدا بزنی:

```text
https://YOUR-RENDER-APP.onrender.com/api/run?token=ADMIN_TOKEN&source=all&keyword_limit=5&city_limit=3&result_limit=8
```

پیشنهاد: روزی 1 یا 2 بار، نه بیشتر.

---

## 7) ساختار دیتابیس

### leads

فیلدهای اصلی:

- `source`
- `entity_type`
- `title`
- `url` با unique constraint
- `keyword`
- `category`
- `city`
- `phone`
- `website`
- `address`
- `rating`
- `review_count`
- `score`
- `status`
- `notes`
- `first_seen`
- `last_seen`

### keywords

کلمات پیش‌فرض مثل:

- فروشگاه بازی
- فروشگاه کنسول
- فروشگاه پلی استیشن
- گیم نت
- گیفت کارت
- سی پی کالاف
- یوسی پابجی
- جم فری فایر

### cities

شهرهای پیش‌فرض با مختصات برای نشان:

- تهران، کرج، مشهد، اصفهان، شیراز، تبریز، اهواز، قم، رشت، کرمانشاه، یزد، ارومیه، ساری، بندرعباس

---

## 8) مسیرهای مهم

```text
GET  /              پنل
POST /run           اجرای کالکتور از پنل
GET  /api/run       اجرای کالکتور برای cron
GET  /export.csv    خروجی CSV
GET  /export.xlsx   خروجی Excel
POST /import.csv    وارد کردن CSV
POST /enrich        غنی‌سازی سایت‌های عمومی و پیدا کردن Instagram/Telegram
GET  /api/leads     لیست JSON لیدها
GET  /health        تست سلامت
```

---

## 9) محدودیت‌ها و انتظار واقعی

این MVP قرار است این‌ها را بدهد:

- لینک عمومی کسب‌وکار/پیج/کانال/آگهی
- عنوان
- توضیح کوتاه
- شهر/آدرس در صورت وجود
- تلفن/سایت فقط اگر API رسمی برگرداند
- امتیاز و دسته‌بندی تقریبی

قرار نیست این‌ها را انجام دهد:

- استخراج شماره خصوصی از پلتفرم‌ها
- لاگین اتوماتیک
- دور زدن کپچا
- ارسال پیام انبوه
- استخراج اعضای کانال/گروه

---

## 10) قدم بعدی پیشنهادی

بعد از deploy و تست API keys، کیفیت خروجی را با این معیار بسنج:

- حداقل 50 لید مرتبط واقعی
- از حداقل 2 منبع
- تکراری کمتر از 30٪
- قابل پیام/تماس دستی

اگر تست جواب داد، فاز بعدی می‌تواند شامل این‌ها باشد:

- داشبورد بهتر
- فیلتر پیشرفته‌تر
- import/export اکسل
- dedupe هوشمند بر اساس نام/تلفن
- صفحه detail برای هر لید
- اضافه کردن صف اجرای background worker


---

## 11) حالت بدون API Key

اگر فعلاً کلید Google/Neshan/SerpAPI نداری، از داخل پنل این گزینه را انتخاب کن:

```text
source = Search Links رایگان
```

این گزینه هیچ سایتی را scrape نمی‌کند و فقط لینک‌های جستجوی دستی می‌سازد؛ مثلاً:

```text
site:t.me فروشگاه بازی تهران
site:instagram.com گیم نت تهران
site:balad.ir فروشگاه کنسول تهران
```

این برای تست پنل، workflow و بررسی دستی خوب است؛ ولی لید واقعی خودکار نیست.

---

## 12) Enrichment قانونی سایت‌ها

اگر از Google Places یا import دستی، برای یک لید فیلد `website` داشته باشیم، گزینه Enrich می‌تواند صفحه عمومی سایت را باز کند و اگر لینک‌های عمومی اینستاگرام یا تلگرام داخل HTML بود، ذخیره کند.

این کار:

- لاگین نمی‌کند
- کپچا را دور نمی‌زند
- اطلاعات خصوصی استخراج نمی‌کند
- فقط HTML عمومی سایت را بررسی می‌کند

---

## 15) اگر SerpAPI در دسترس نبود

SerpAPI برای بعضی کشورها یا ثبت‌نام‌ها ممکن است شماره/پرداخت بخواهد. پروژه به SerpAPI وابسته نیست و این جایگزین‌ها را هم پشتیبانی می‌کند:

- `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID`
- `BRAVE_SEARCH_API_KEY`
- `SERPER_API_KEY`
- `SEARCHAPI_KEY`
- `TAVILY_API_KEY`

داخل پنل گزینه `Web Search APIها` همه providerهای فعال را اجرا می‌کند. اگر فقط Brave را ست کرده باشی، فقط Brave اجرا می‌شود؛ اگر Google CSE و Brave هر دو ست باشند، هر دو اجرا می‌شوند.

# B2 Magnet Downloader - Complete Package

פרויקט מלא להורדת Magnet מורשה, דחיסה אופציונלית והעלאה ל-Backblaze B2 באמצעות ה-B2 Native API בלבד.

## נתיבים נוכחיים

נתיב הפרויקט המקומי:

```text
C:\Users\USER\b2-magnet-downloader-complete
```

נתיבי הקבצים המרכזיים:

```text
C:\Users\USER\b2-magnet-downloader-complete\main.py
C:\Users\USER\b2-magnet-downloader-complete\Dockerfile
C:\Users\USER\b2-magnet-downloader-complete\docker-compose.yml
C:\Users\USER\b2-magnet-downloader-complete\requirements.txt
C:\Users\USER\b2-magnet-downloader-complete\.env.example
C:\Users\USER\b2-magnet-downloader-complete\n8n-workflow-b2.json
```

כתובת האפליקציה ב-Azure:

```text
https://b2-magnet-downloader.bluepebble-345273df.northeurope.azurecontainerapps.io
```

## מה יש בתיקייה

- `main.py` - שירות FastAPI.
- `requirements.txt` - תלויות Python.
- `Dockerfile` - בניית Docker image.
- `docker-compose.yml` - הגדרת container.
- `.env.example` - תבנית הגדרות ללא סודות.
- `n8n-workflow-b2.json` - workflow מוכן לייבוא ל-n8n.
- `.dockerignore` ו-`.gitignore` - מניעת הכנסת סודות וקבצים זמניים.

## הגדרת Backblaze B2

1. היכנס ל-`https://secure.backblaze.com/`.
2. צור Bucket פרטי.
3. צור Application Key עם הרשאות לקריאה וכתיבה ב-Bucket.
4. העתק את `.env.example` ל-`.env`.
5. מלא ב-`.env`:

```text
B2_BUCKET_NAME=שם-ה-Bucket
B2_APPLICATION_KEY_ID=ה-Key-ID
B2_APPLICATION_KEY=ה-Application-Key
B2_OBJECT_PREFIX=magnet-downloads
DOWNLOAD_DIR=/app/downloads
```

אין להשתמש ב-`B2_ENDPOINT`, ב-`boto3` או במפתחות AWS. זהו חיבור B2 Native.

## הרצה ב-Azure Container Apps

ב-Azure Container App הגדר את image:

```text
magnetb2registry123.azurecr.io/b2-magnet-downloader:latest
```

הגדר Ingress חיצוני עם פורט `8000` והוסף את משתני B2. את שני המפתחות הגדר כ-Secrets.

אחרי הפריסה בדוק:

```text
https://כתובת-היישום/health
```

## API

`POST /download`:

```json
{
  "magnet_link": "magnet:?xt=urn:btih:...",
  "filename": "my-download",
  "add_to_zip": false,
  "timeout": 600
}
```

הערך `false` מעלה כל קובץ בנפרד. הערך `true` יוצר ZIP אחד.

לאחר קבלת `job_id`:

```text
GET https://b2-magnet-downloader.bluepebble-345273df.northeurope.azurecontainerapps.io/status/{job_id}
GET https://b2-magnet-downloader.bluepebble-345273df.northeurope.azurecontainerapps.io/logs/{job_id}
GET https://b2-magnet-downloader.bluepebble-345273df.northeurope.azurecontainerapps.io/health
```

הנתיב לשליחת הורדה חדשה הוא:

```text
POST https://b2-magnet-downloader.bluepebble-345273df.northeurope.azurecontainerapps.io/download
```

התוצאה מחזירה `bucket`, `file_id`, `file_name`, `object_name` וגודל. עבור Bucket פרטי, יש להשתמש במנגנון authorization של B2 כדי להוריד את הקובץ.

## n8n

1. פתח n8n.
2. בחר `Import from File`.
3. בחר את `n8n-workflow-b2.json`.
4. הפעל את ה-Form Trigger.
5. השתמש רק בקישורי Magnet שיש לך הרשאה חוקית להוריד.

## אבטחה

לפני חשיפה לאינטרנט הוסף API key או authentication, rate limiting והגבלת גודל/מספר jobs. אין לשמור `.env` ב-GitHub ואין להכניס מפתחות לתוך Docker image.

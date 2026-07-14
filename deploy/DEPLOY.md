# Deployment — TaxBuddy Digital Invoicing (VPS, Ubuntu)

## 1. Server prep
    sudo apt update && sudo apt install -y python3-venv python3-dev \
        default-libmysqlclient-dev build-essential nginx mysql-server
    sudo mkdir -p /srv/taxbuddy-invoicing /var/log/taxbuddy
    sudo chown www-data:www-data /var/log/taxbuddy

## 2. Code + venv
    cd /srv/taxbuddy-invoicing
    # (git clone ya zip extract yahan)
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt gunicorn

## 3. Environment  (.env — .env.example copy kar ke bharien)
    DJANGO_SECRET_KEY=<50-char random — python -c "import secrets;print(secrets.token_urlsafe(50))">
    DJANGO_DEBUG=0
    DJANGO_ALLOWED_HOSTS=invoicing.taxbuddyumair.com
    DB_NAME=digital-invoicing  DB_USER=taxbuddy  DB_PASSWORD=<strong>
    FBR_USE_MOCK=0            # PRAL token ke baad; tab tak 1

## 4. Database
    mysql: CREATE DATABASE `digital-invoicing` CHARACTER SET utf8mb4;
           CREATE USER 'taxbuddy'@'localhost' IDENTIFIED BY '<strong>';
           GRANT ALL ON `digital-invoicing`.* TO 'taxbuddy'@'localhost';
    python manage.py migrate
    python manage.py import_hs_codes        # HS directory seed
    python manage.py createsuperuser
    python manage.py collectstatic --noinput

## 5. Services
    sudo cp deploy/gunicorn.service /etc/systemd/system/taxbuddy-invoicing.service
    sudo systemctl daemon-reload && sudo systemctl enable --now taxbuddy-invoicing
    sudo cp deploy/nginx.conf /etc/nginx/sites-available/taxbuddy-invoicing
    sudo ln -s /etc/nginx/sites-available/taxbuddy-invoicing /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo apt install -y certbot python3-certbot-nginx
    sudo certbot --nginx -d invoicing.taxbuddyumair.com

## 6. Backups (LAZMI — tax records)
    # nightly dump (crontab -e):
    0 2 * * * mysqldump digital-invoicing | gzip > /srv/backups/di-$(date +\%F).sql.gz
    # 30 din retention + off-server copy zaroor rakhein.

## 7. Update deploy (har release)
    source venv/bin/activate && pip install -r requirements.txt
    python manage.py migrate && python manage.py collectstatic --noinput
    sudo systemctl restart taxbuddy-invoicing

## 8. Go-live checklist
    [ ] DJANGO_DEBUG=0, naya SECRET_KEY, strong DB password
    [ ] HTTPS active (settings auto-hardening: HSTS, secure cookies)
    [ ] Har business ke SellerProfile mein PRAL token + use_sandbox sahi
    [ ] Sandbox scenarios PRAL certification pass (Manual v1.6 §4.1)
    [ ] FBR_USE_MOCK=0 sirf certification ke BAAD
    [ ] Backup cron test-restore verified

## FBR Reference Sync (Milestone 4)
Rozana FBR ke transaction types + rates ke saath sync (Tech Spec §5.5/§5.8).
Unambiguous rate changes date-effective rows ke zariye apply hote hain —
purana data kabhi delete nahi hota. Multi-rate types (e.g. Eighth Schedule)
sirf report hote hain; admin se decide karein.

```cron
15 6 * * *  cd /srv/taxbuddy-invoicing && ./venv/bin/python manage.py sync_fbr_reference --apply >> logs/ref_sync.log 2>&1
```

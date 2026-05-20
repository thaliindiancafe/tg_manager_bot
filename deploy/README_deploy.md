# Деплой tg-manager-agent на Beget VPS (Ubuntu)

Инструкция для production-запуска бота в режиме webhook (`DEV_MODE=false`).

## Требования

- VPS Beget с Ubuntu 22.04+ (или 20.04)
- Домен, указывающий на IP сервера (A-запись)
- Открытые порты: `22` (SSH), `80` и `443` (для Let's Encrypt и webhook)
- Service account Google с доступом к Sheets, Calendar, Drive
- Токен Telegram-бота и ключ Gemini API

---

## 1. Подключение по SSH

```bash
ssh root@YOUR_SERVER_IP
```

Замените `YOUR_SERVER_IP` на IP вашего VPS.

---

## 2. Установка Python 3.11 и pip

```bash
apt update
apt install -y software-properties-common git curl nginx certbot python3-certbot-nginx
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.11 python3.11-venv python3.11-dev
```

Проверка:

```bash
python3.11 --version
```

---

## 3. Клонирование репозитория

```bash
mkdir -p /opt/tg-manager-agent
cd /opt

# Замените URL на ваш репозиторий
git clone https://github.com/YOUR_USER/tg-manager-agent.git tg-manager-agent
cd /opt/tg-manager-agent
```

Создайте пользователя для сервиса:

```bash
useradd --system --home /opt/tg-manager-agent --shell /usr/sbin/nologin tgagent
chown -R tgagent:tgagent /opt/tg-manager-agent
```

---

## 4. Создание `.env` на сервере

```bash
cp .env.example .env
nano .env
```

Заполните переменные (production):

```env
BOT_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
SPREADSHEET_ID=id_новой_таблицы_бота
SOURCE_SPREADSHEET_ID=id_старой_таблицы_клиента
GOOGLE_CREDENTIALS_JSON=secrets/service_account.json
DRIVE_KNOWLEDGE_FOLDER_ID=id_папки_drive
WEBHOOK_URL=https://yourdomain.com/webhook
WEBHOOK_SECRET=random_secret_string_min_16_chars
TIMEZONE=Europe/Moscow
DEV_MODE=false
```

Положите ключ service account:

```bash
mkdir -p secrets
nano secrets/service_account.json
chmod 600 secrets/service_account.json .env
chown -R tgagent:tgagent /opt/tg-manager-agent
```

---

## 5. Установка зависимостей

```bash
cd /opt/tg-manager-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Проверка Google API (опционально):

```bash
python scripts/test_google_api.py
python scripts/setup_sheets.py
```

---

## 6. SSL через Let's Encrypt (certbot)

Создайте конфиг Nginx (замените `yourdomain.com`):

```bash
nano /etc/nginx/sites-available/tg-agent
```

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location /webhook {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте сайт:

```bash
ln -s /etc/nginx/sites-available/tg-agent /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

Выпустите сертификат:

```bash
certbot --nginx -d yourdomain.com
```

После certbot URL в `.env` должен быть HTTPS:

```env
WEBHOOK_URL=https://yourdomain.com/webhook
```

---

## 7. Настройка systemd

```bash
cp /opt/tg-manager-agent/deploy/tg-agent.service /etc/systemd/system/tg-agent.service
systemctl daemon-reload
```

Проверьте пути в unit-файле:

- `WorkingDirectory=/opt/tg-manager-agent`
- `ExecStart=/opt/tg-manager-agent/.venv/bin/python main.py`
- `EnvironmentFile=/opt/tg-manager-agent/.env`

---

## 8. Запуск сервиса

```bash
systemctl enable tg-agent
systemctl start tg-agent
systemctl status tg-agent
```

Ожидаемый статус: `active (running)`.

В unit-файле настроено автоперезапускание:

- `Restart=always`
- `RestartSec=10`

---

## 9. Проверка логов

```bash
journalctl -u tg-agent -f
```

Ищите строки:

- `Starting bot in webhook mode on port 8080`
- `Webhook registered: https://yourdomain.com/webhook`
- `Scheduler started: run_automations every 5 minutes`

---

## 10. Регистрация webhook в Telegram

```bash
curl "https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}&secret_token={WEBHOOK_SECRET}"
```

Пример:

```bash
curl "https://api.telegram.org/bot123456:ABC-DEF/setWebhook?url=https://yourdomain.com/webhook&secret_token=your_secret_from_env"
```

Проверка:

```bash
curl "https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
```

---

## Полезные команды

```bash
# Перезапуск после обновления кода
cd /opt/tg-manager-agent
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart tg-agent

# Остановка
systemctl stop tg-agent

# Миграция графика (разово или по cron)
python scripts/migrate_schedule.py --test
python scripts/migrate_schedule.py
```

---

## Чеклист перед запуском

- [ ] Privacy Mode отключён в @BotFather
- [ ] Service account добавлен в Google Sheets (старая — Читатель, новая — Редактор)
- [ ] Service account имеет доступ к Calendar и папке Drive
- [ ] В таблице бота созданы 8 листов (`python scripts/setup_sheets.py`)
- [ ] `DEV_MODE=false` в `.env`
- [ ] `WEBHOOK_URL` совпадает с путём в Nginx (`/webhook`)
- [ ] Webhook зарегистрирован через `setWebhook`

<div align="center">

# **AWG Toolza: LucX Edition**

**Менеджер AmneziaWG 2.0** — VPN с DPI-обходом одной командой.<br>
3 уровня обфускации · 5 профилей мимикрии · CPS-генератор · Warp · Xray · Каскад · Telegram-бот

<br>

[![License: MIT](https://img.shields.io/badge/License-MIT-ffffff?style=flat-square&labelColor=000000)](https://opensource.org/licenses/MIT)
[![License: PolyForm](https://img.shields.io/badge/LucX%20Edition-PolyForm%20NC-blueviolet?style=flat-square)](#-лицензия)
[![Platform](https://img.shields.io/badge/Ubuntu%2024%20%2F%20Debian%2012%2B-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![Protocol](https://img.shields.io/badge/AWG-2.0%20only-00d4ff?style=flat-square)](#)
[![Version](https://img.shields.io/badge/version-6.9.1--lucx-ff6b00?style=flat-square)](#)

<br>

</div>

---

## 🚀 Быстрый старт

```bash
sudo curl -fsSL https://raw.githubusercontent.com/AlexeyLCP/awg-multi-script/main/awg2.sh \
  -o /usr/local/bin/awg2 && sudo chmod +x /usr/local/bin/awg2 && sudo awg2
```

Повторный запуск в любой момент:
```bash
sudo awg2
```

---

## 📋 Полное меню

```
╔══════════════════════════════════════════════╗
║    AWG Toolza: LucX Edition v6.9.1           ║
║   AWG 2.0 — QUIC / WebRTC / SIP / DNS        ║
║       + Warp / Xray / Каскад туннели         ║
╚══════════════════════════════════════════════╝
  IP сервера : 1.2.3.4
  Порт       : 41300
  Интерфейс  : ● активен
  Клиентов   : 5

  1)  Сервер          — установка
  2)  Клиенты         — управление
  3)  Диагностика     — тест, домены
  4)  Бекапы          — создать, восстановить
  5)  Туннели и DNS   — Warp, DNS, каскад, Xray, tun2socks
  6)  Telegram-бот    — управление ботом
  7)  Удаление и сброс — вроде понятно
  8)  Обновить скрипт  — загрузить с GitHub

   0) Выход
```

## Профили и мимикрия

При создании сервера (Сервер → 2) выбирается профиль:

| Профиль | Обфускация | Мимикрия I1 |
|---|---|---|
| **Lite** | базовая | DNS (icloud.com) |
| **Standard** | сбалансированная | **TLS ClientHello** |
| **Pro** | на выбор (без I1 / +I1 / +I1-I5) | TLS / DNS / SIP / QUIC |

Профили мимикрии (Pro, выбор уровня I1–I5):

- **TLS** — браузерный ClientHello (Chrome-like, SNI из пула). **Рекомендуется в РФ 2026** — выглядит как обычный заход на сайт, DPI его не режет.
- **DNS** — DNS Query c EDNS0, рандомный TXID. Компактный, надёжный.
- **SIP** — REGISTER-запрос (VoIP мимикрия).
- **QUIC** — Chrome-like Initial 1200B + Short Header. ⚠ В РФ 2026 ловят по сигнатуре Initial-пакета — используй осознанно.

> CPS-пакеты I1–I5 — это **клиентские** параметры. В серверный `awg0.conf` они не пишутся (там только Jc/Jmin/Jmax/S/H). Разные клиенты могут иметь разную мимикрию на одном сервере.

---

---

## ☁ Warp туннель Cloudflare (Туннели и DNS → 1)

Обход блокировки IP сервера. Когда ТСПУ блокирует IP Hetzner/OVH — выходной трафик оборачивается через Cloudflare Warp.

```
Клиент РФ → AWG → твой сервер → Cloudflare → интернет
```

SSH и серверный трафик идут **напрямую** — не через Warp.

**Меню Warp:**
```
1) Установить wgcf и зарегистрировать Warp
2) Активировать Warp+ (лицензионный ключ)
3) Включить туннель
4) Выключить туннель
5) Перегенерировать профиль
6) Управление клиентами в Warp   ← кто через Warp, кто напрямую
7) Health-check + auto-failover  ← если упал — direct routing
8) Импорт wgcf-profile.conf      ← если регистрация блокируется
9) Поиск рабочего endpoint       ← если UDP к Cloudflare режется
d) Удалить Warp полностью
```

**Где взять Warp+ ключ:** приложение **1.1.1.1** → Аккаунт → Ключ.

**Если Warp не подключается на РФ хостинге:**
1. `Туннели и DNS → Warp → 8` — импорт готового профиля (например, через Google Cloud Shell)
2. `Туннели и DNS → Warp → 9` — автоматический поиск рабочего endpoint
3. Если ничего не помогло — провайдер режет UDP к Cloudflare, нужен другой хостинг

---

## 🔐 Шифрованный DNS (Туннели и DNS → 2)

Все DNS-запросы клиентов идут через DoH (DNS-over-HTTPS) к Cloudflare / Google / Quad9 с DNSSEC.

```
Клиент → AWG → DNAT iptables → dnscrypt-proxy → DoH → Cloudflare/Google/Quad9
                (порт 53)       (127.0.0.1:5300)       (HTTPS зашифрованно)
```

**Что даёт:**
- Защита от DNS-leak (провайдер сервера не видит резолвируемые домены)
- DNSSEC защищает от подмены DNS-ответов
- No-logging резолверы

**Меню DNS:**
```
1) Включить (установить + настроить)
2) Перезапустить сервис
3) Логи
4) Сменить upstream (Cloudflare / Google / Quad9 / комбинации)
5) Выключить и удалить
```

---

## 🌉 Каскад (Туннели и DNS → 3)

Простой iptables DNAT/SNAT: пробрасываем произвольный порт с вашего сервера на IP:Port зарубежного VPS. Не требует установки ПО на втором сервере.

```
Клиент → AWG → твой сервер → (iptables DNAT) → зарубежный VPS → интернет
```

**Меню Каскад:**
```
1) Добавить правило (один порт)
2) Добавить кастомное правило (разные порты)
3) Список правил
4) Удалить одно правило
5) Сбросить все правила каскада
6) Диагностика (полный дамп для отладки)
7) Экспорт для поддержки
d) Удалить модуль каскада полностью
```

> **Совет:** Если нужно скрыть трафик от DPI — используйте **пункт 18 (Xray)** вместо Каскада. Xray шифрует и обфусцирует трафик, Каскад — нет.

---

## 🔭 Xray туннель [LucX Edition] (Туннели и DNS → 4)

Самый мощный инструмент для обхода DPI. Когда ТСПУ режет и AmneziaWG, и Cloudflare — трафик AWG-клиентов маскируется под обычный HTTPS к Google/Drive через Xray с протоколом VLESS+REALITY.

```
Клиент → AWG → сервер → Xray (TUN/gvisor) → VLESS+REALITY → прокси → интернет
                                            → VLESS+REALITY → прокси    (балансировка)
```

**Особенности:**
- **TUN-вход Xray** — встроенный gvisor-стек, без tun2proxy
- **Поддержка протоколов:** `vless://` · `vmess://` · `hysteria2://` / `hy2://`
- **Стратегии балансировки:** random / roundRobin / leastPing / leastLoad
- **Auto-observatory** — автоматическая генерация для leastPing/leastLoad
- **Selective routing** — выбор клиентов, которые ходят через Xray
- **SOCKS5 на 127.0.0.1:10101** — для перенаправления других приложений
- **Динамический MTU** — подхватывается с awg0 автоматически
- **Защита от дублей outbounds** в конфиге Xray

**Меню Xray:**
```
1) Установить Xray
2) Добавить outbound (vless:// / vmess:// / hysteria2://)
3) Удалить outbound
4) Настроить балансировщик (стратегия)
5) Включить туннель
6) Выключить туннель
7) Перезапустить туннель
8) Управление клиентами в Xray
```

Xray и Warp **не могут работать одновременно** — выбери что-то одно.

---


---

## 🧦 tun2socks прокси (Туннели и DNS → 5)

Позволяет завернуть весь трафик клиентов AWG в локальный или удаленный SOCKS5/HTTP прокси. Отлично подходит для полной изоляции трафика сервера, заворачивая его в сторонние сети (например, через Xray/sing-box/Nekobox, запущенные локально на сервере, или SSH-туннель).

```
Клиент → AWG → tun0 (tun2socks) → SOCKS5 прокси → интернет
```

**Особенности:**
- Трафик клиентов прозрачно перенаправляется в указанный прокси-сервер.
- Реализован корректный NAT (`MASQUERADE`) и `iptables` правила для `tun0`.
- Работает как системный сервис (`awg-tun2socks.service`), автоматически поднимается после рестарта.
- Скрипт корректно обходит этот туннель при генерации новых клиентских конфигов, так что в поле `Endpoint` по-прежнему будет реальный публичный IP сервера, а не IP прокси.
- Имеет взаимную блокировку с **Warp** и **Xray** (одновременно может работать только один глобальный роутинг).

**Меню tun2socks:**
```
1) Включить туннель (с запросом IP:PORT, по умолчанию 127.0.0.1:10808)
2) Выключить туннель
3) Просмотр логов
```

## 🤖 Telegram-бот (Главное меню → 6)

Управление сервером со смартфона через inline-меню без SSH.

**Возможности:**
- Список клиентов со статусом (🟢/🟡/🔴/⚫ онлайн, трафик, последний handshake), с пагинацией (◀️ ▶️) — корректно работает при сотнях клиентов
- 🚨 **Уведомление при отвале клиента** — моментальный алерт со звуком (имя, IP, заметка, время)
- Добавить клиента с выбором профиля мимикрии прямо в боте
- QR-код / текст / .conf файл по любому клиенту
- 📝 **Заметки к клиентам** — произвольный текст или кликабельная ссылка (до 200 символов)
- ☁️ Вкл/выкл WARP для клиента из карточки
- ⏰ **Срок действия клиента** — auto-suspend по истечении, уведомление за 1 час
- Удалить клиента с подтверждением
- Статус сервера

**Установка:**

Через главное меню: `sudo awg2` → **6) Telegram-бот** → Установить.

Или вручную:
```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/AlexeyLCP/awg-multi-script/main/awg-bot-install.sh \
  -o /tmp/awg-bot-install.sh && bash /tmp/awg-bot-install.sh'
```

Установщик спросит:
1. **Bot Token** — получи у [@BotFather](https://t.me/BotFather): `/newbot` → имя → токен
2. **Telegram ID** — узнать через [@userinfobot](https://t.me/userinfobot)

Бот запускается как systemd-сервис (`awg-bot.service`) и поднимается при перезагрузке.

**Команды бота:** `/start` `/status` `/help` `/id`

**Управление:**
```bash
sudo systemctl status awg-bot
sudo systemctl restart awg-bot
sudo journalctl -u awg-bot -f
```

---

## 📂 Файлы

| Путь | Назначение |
|---|---|
| `/etc/amnezia/amneziawg/awg0.conf` | Серверный конфиг AWG |
| `/root/<имя>_awg2.conf` | Клиентские конфиги |
| `/var/log/awg-Toolza.log` | Основной лог |
| `~/awg_backup/` | Бекапы |
| `/etc/xray/config.json` | Конфиг Xray (LucX Edition) |
| `/etc/awg2-cascade/rules` | Правила Каскада |
| `/usr/local/bin/awg-bot.py` | Код Telegram-бота |
| `/etc/awg-bot.conf` | Токен и chat_id бота |
| `/etc/systemd/system/awg-bot.service` | Сервис бота |

---

## 📱 Импорт на клиенте

[**AmneziaVPN**](https://amnezia.org) (Android / iOS / macOS / Windows / Linux):
- **QR** — Клиенты → 1 (Управление), сканируй с терминала
- **Текст** — Клиенты → 1 (Управление) для больших конфигов (QUIC Full)
- **Файл** — `Добавить туннель → Из файла` → передай `/root/<имя>_awg2.conf` через scp

[**AmneziaWG**](https://github.com/amnezia-vpn/amneziawg-windows-client) (нативный клиент AWG):
- [Android](https://play.google.com/store/apps/details?id=org.amnezia.awg)
- [iOS](https://apps.apple.com/app/amneziawg/id6478942365)
- [Windows](https://github.com/amnezia-vpn/amneziawg-windows-client/releases)

[**Keenetic**](https://docs.amnezia.org/documentation/instructions/keenetic-os-awg) — KeeneticOS 4.x+ или AWG Manager на Entware

---

## 🔍 Проверка конфига

Проверить `.conf` на DPI-стойкость можно через [AWG Analyzer](https://alexeylcp.github.io/awg-analyzer/) — полностью локальный JS-инструмент:

- Детект версии (WireGuard / AWG 1.0 / 1.5 / 2.0) + уровень обфускации
- Глубокий разбор I1-I5 (валидность `<b 0x...>`, лимит `<r>`, протокол)
- Проверка H1-H4 sub-квадрантов (12/12 = идеал)
- Security / Stealth / DPI score + пошаговый upgrade path

---

## 🙏 Благодарности

**AWG Toolza: LucX Edition** — форк [AWG Toolza](https://github.com/pumbaX/awg-multi-script) от [pumbaX](https://github.com/pumbaX). Огромное спасибо за исходный проект и огромную работу по внедрению AmneziaWG в простой bash-скрипт.

**Использованные проекты:**
- **[pumbaX/awg-multi-script](https://github.com/pumbaX/awg-multi-script)** — основа этого форка (MIT)
- **[XTLS/Xray-core](https://github.com/XTLS/Xray-core)** — движок для Xray туннеля (MPL-2.0)
- **[amnezia-vpn](https://github.com/amnezia-vpn)** — AmneziaWG протокол и клиенты
- **[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)** — GeoIP/GeoSite базы
- **[@awgToolza](https://t.me/awgToolza)** — сообщество, тесты и фидбек
- **[@awgmanager](https://t.me/awgmanager)** — AWG Manager, вдохновение

**Другие проекты автора:**
- **[AWG Toolza: LucX Edition](https://github.com/AlexeyLCP/awg-multi-script)** — этот репозиторий
- **[AWG Analyzer](https://alexeylcp.github.io/awg-analyzer/)** — онлайн-анализатор конфигов AWG
- **[LucX-UI](https://github.com/AlexeyLCP/lucx-ui)** — форк 3x-ui с нативной интеграцией AWG, Telemt и DPI-пресетами для России 2026
- **[Angry-BOX](https://github.com/alexeylcp/angry-box)** — SSH-оркестратор для sing-box/xray с пресетами обфускации 2026 (Россия / Иран / Китай)

---

## ☕ Поддержать автора LucX Edition

Разработка форка, интеграция Xray, Hysteria2, Каскада и других функций занимает много времени. Если проект оказался полезен:

[![YooMoney](https://img.shields.io/badge/YooMoney-поддержать-blue?style=flat-square&logo=yandex)](https://yoomoney.ru/to/41001989176429)

---

## 📄 Лицензия

Оригинальный **AWG Toolza** (`awg2.sh` — базовый функционал): **MIT License**.

Доработки **LucX Edition** (интеграция Xray, Hysteria2, Каскад и пр.): **PolyForm Noncommercial 1.0.0** — разрешено личное, некоммерческое, образовательное использование. Коммерческое использование требует письменного разрешения автора.

---

<div align="center">

*Сообщество: [AWG-Toolza](https://t.me/awgToolza)*

**AWG Toolza: LucX Edition v6.9.1** · Форк от [alexeylcp](https://github.com/AlexeyLCP)

</div>

# battery-tray

Іконки в треї Windows із відсотком заряду бездротових пристроїв — окрема іконка на кожен пристрій.

Підтримується:
- **Bluetooth** — усе, для чого Windows сама показує заряд у «Параметри → Bluetooth і пристрої».
- **Logitech через USB-донгл** (Lightspeed / Unifying / Bolt) — протокол HID++ 2.0.

## Встановлення

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Запуск

```powershell
.\.venv\Scripts\pythonw.exe -m battery_tray          # у треї, без консолі
.\.venv\Scripts\python.exe  -m battery_tray --once   # надрукувати знайдені пристрої й вийти
```

Клік по іконці відкриває налаштування: які пристрої показувати, як їх назвати і скільки хвилин
тримати іконку після відключення. Пристрої з однаковою назвою показуються однією іконкою.
Автозапуск вмикається там само або в меню іконки.

Кольори цифри: звичайний — заряд у нормі, червоний — мало, зелений — заряджається, сірий — пристрій відключено.

- Налаштування: `%APPDATA%\battery-tray\settings.json`
- Лог: `%LOCALAPPDATA%\battery-tray\battery-tray.log`

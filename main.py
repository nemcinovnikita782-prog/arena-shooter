from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Rectangle, Line
from kivy.clock import Clock
import socket
import threading
import json
import random
import math
import os
import time

SAVE_FILE = "arena_shooter_profile.json"
UDP_PORT = 5556
TCP_PORT = 5555

PLAYER_COLORS = [
    (0.23, 0.51, 0.96, 1),
    (0.13, 0.77, 0.37, 1),
    (0.85, 0.46, 0.15, 1),
    (0.16, 0.65, 0.68, 1),
    (0.55, 0.45, 0.75, 1)
]

def get_save_path():
    app = App.get_running_app()
    if app and hasattr(app, "user_data_dir"):
        return os.path.join(app.user_data_dir, "arena_shooter_profile.json")
    return "arena_shooter_profile.json"


def load_profile():
    default_data = {
        "nickname": "",
        "best_score": 0,
        "coins": 0,
        "total_kills": 0,
        "beta_player": True,
        "controls": {
            "move_rx": 0.16,
            "move_ry": 0.22,
            "shoot_rx": 0.84,
            "shoot_ry": 0.22,
            "joy_radius": 52.0,
            "auto_fire": True
        }
    }
    path = get_save_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_data.update(saved)
        except Exception:
            pass
    return default_data


def save_profile(data):
    try:
        path = get_save_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def only_digits_and_dots(substring, from_undo):
    """Поле ввода: разрешаем только цифры и точки (код комнаты или IP-адрес)."""
    return "".join(ch for ch in substring if ch.isdigit() or ch == ".")


def is_ip_address(text):
    parts = text.strip().split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or int(p) > 255:
            return False
    return True


def describe_net_error(e):
    """Понятное объяснение сетевой ошибки для экрана."""
    text = str(e)
    low = text.lower()
    if isinstance(e, socket.timeout):
        return "нет ответа (проверь, что вы в одной сети)"
    if isinstance(e, ConnectionRefusedError):
        return "хост не принимает подключения (комната создана?)"
    if isinstance(e, PermissionError) or "denied" in low:
        return text + ". Проверь разрешение INTERNET в buildozer.spec"
    if "unreachable" in low:
        return "сеть недоступна (включи Wi-Fi или точку доступа)"
    return text


def _interfaces_via_jnius():
    """Сетевые интерфейсы телефона с их адресами (только Android, через pyjnius)."""
    result = []
    try:
        from jnius import autoclass
        NetworkInterface = autoclass("java.net.NetworkInterface")
        ifs = NetworkInterface.getNetworkInterfaces()
        while ifs is not None and ifs.hasMoreElements():
            ni = ifs.nextElement()
            try:
                if ni.isLoopback() or not ni.isUp():
                    continue
                addrs = ni.getInterfaceAddresses()
                for i in range(addrs.size()):
                    ia = addrs.get(i)
                    ip = str(ia.getAddress().getHostAddress())
                    if ":" in ip:
                        continue
                    b = ia.getBroadcast()
                    result.append({
                        "name": str(ni.getName()),
                        "ip": ip,
                        "bcast": str(b.getHostAddress()) if b is not None else None
                    })
            except Exception:
                continue
    except Exception:
        return []
    return result


def _ips_via_route_lookup():
    """Запасной способ узнать свой IP: спрашиваем у системы маршрут (пакеты не отправляются)."""
    ips = []
    for target in ("8.8.8.8", "192.168.43.1", "192.168.0.1", "192.168.1.1", "10.0.0.1"):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 9))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip != "0.0.0.0" and ip not in ips:
                ips.append(ip)
        except Exception:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    return ips


def get_network_info():
    """Возвращает (ips, broadcasts): наши адреса в локальной сети и адреса для рассылки."""
    ips, bcasts = [], []
    for it in _interfaces_via_jnius():
        if it["bcast"]:
            if it["ip"] not in ips:
                ips.append(it["ip"])
            if it["bcast"] not in bcasts:
                bcasts.append(it["bcast"])
    if not bcasts:
        for ip in _ips_via_route_lookup():
            if ip not in ips:
                ips.append(ip)
            b = ".".join(ip.split(".")[:3] + ["255"])
            if b not in bcasts:
                bcasts.append(b)
    if "255.255.255.255" not in bcasts:
        bcasts.append("255.255.255.255")
    return ips, bcasts


class ArenaCanvas(Widget):
    def __init__(self, game, **kwargs):
        super().__init__(**kwargs)
        self.game = game
        self.move_touch_id = None
        self.shoot_touch_id = None
        self.move_knob = (0, 0)
        self.shoot_knob = (0, 0)
        self.aim_dir = (0, 0)
        self.manual_aim_active = False
        self.bind(pos=self.redraw, size=self.redraw)

    def get_joy_centers(self):
        ctrl = self.game.profile["controls"]
        mx = self.x + self.width * ctrl["move_rx"]
        my = self.y + self.height * ctrl["move_ry"]
        sx = self.x + self.width * ctrl["shoot_rx"]
        sy = self.y + self.height * ctrl["shoot_ry"]
        return (mx, my), (sx, sy), ctrl["joy_radius"]

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if not self.game.match_active or self.game.state["game_over"] or self.game.paused:
            return True

        (mx, my), (sx, sy), rad = self.get_joy_centers()
        hit_rad = rad * 1.6

        if math.hypot(touch.x - mx, touch.y - my) <= hit_rad and self.move_touch_id is None:
            self.move_touch_id = touch.uid
            self.update_move_stick(touch.x, touch.y, mx, my, rad)
            return True

        if math.hypot(touch.x - sx, touch.y - sy) <= hit_rad and self.shoot_touch_id is None:
            self.shoot_touch_id = touch.uid
            self.manual_aim_active = True
            self.update_shoot_stick(touch.x, touch.y, sx, sy, rad)
            return True

        mid_x = self.x + self.width * 0.5
        if touch.x <= mid_x and self.move_touch_id is None:
            self.move_touch_id = touch.uid
            self.update_move_stick(touch.x, touch.y, mx, my, rad)
        elif self.shoot_touch_id is None:
            self.shoot_touch_id = touch.uid
            self.manual_aim_active = True
            self.update_shoot_stick(touch.x, touch.y, sx, sy, rad)
        return True

    def update_move_stick(self, tx, ty, mx, my, rad):
        dx = tx - mx
        dy = ty - my
        dist = math.hypot(dx, dy)
        if dist == 0:
            self.move_knob = (mx, my)
            self.game.set_move_dir(0, 0)
            return
        clamped = min(dist, rad)
        nx, ny = dx / dist, dy / dist
        self.move_knob = (mx + nx * clamped, my + ny * clamped)
        if dist > 6:
            self.game.set_move_dir(nx, ny)
        else:
            self.game.set_move_dir(0, 0)

    def update_shoot_stick(self, tx, ty, sx, sy, rad):
        dx = tx - sx
        dy = ty - sy
        dist = math.hypot(dx, dy)
        if dist == 0:
            self.shoot_knob = (sx, sy)
            return
        clamped = min(dist, rad)
        nx, ny = dx / dist, dy / dist
        self.shoot_knob = (sx + nx * clamped, sy + ny * clamped)
        if dist > 8:
            self.aim_dir = (nx, ny)
            self.game.set_manual_aim(True, nx, ny)
    def on_touch_move(self, touch):
        (mx, my), (sx, sy), rad = self.get_joy_centers()
        if touch.uid == self.move_touch_id:
            self.update_move_stick(touch.x, touch.y, mx, my, rad)
        elif touch.uid == self.shoot_touch_id:
            self.update_shoot_stick(touch.x, touch.y, sx, sy, rad)
        return True

    def on_touch_up(self, touch):
        if touch.uid == self.move_touch_id:
            self.move_touch_id = None
            self.game.set_move_dir(0, 0)
        elif touch.uid == self.shoot_touch_id:
            self.shoot_touch_id = None
            self.manual_aim_active = False
            self.game.set_manual_aim(False, 0, 0)
        return True

    def redraw(self, *args):
        self.canvas.clear()
        st = self.game.state
        ox, oy = self.x, self.y
        w, h = self.width, self.height

        with self.canvas:
            Color(0.11, 0.13, 0.15, 1)
            Rectangle(pos=(ox, oy), size=(w, h))

            Color(0.16, 0.19, 0.22, 1)
            for gx in range(0, int(w), 60):
                Line(points=[ox + gx, oy, ox + gx, oy + h], width=1)
            for gy in range(0, int(h), 60):
                Line(points=[ox, oy + gy, ox + w, oy + gy], width=1)

            Color(0.0, 0.85, 0.45, 0.5)
            Line(rectangle=(ox + 4, oy + 4, w - 8, h - 8), width=2)

            for obs in st["obstacles"]:
                rx = ox + obs["rx"] * w
                ry = oy + obs["ry"] * h
                rw = obs["rw"] * w
                rh = obs["rh"] * h
                Color(0.07, 0.08, 0.09, 0.7)
                Rectangle(pos=(rx + 4, ry - 4), size=(rw, rh))
                Color(0.32, 0.36, 0.42, 1)
                Rectangle(pos=(rx, ry), size=(rw, rh))
                Color(0.45, 0.50, 0.58, 1)
                Line(rectangle=(rx, ry, rw, rh), width=1.2)

            for dr in st["drops"]:
                dx, dy = ox + dr["x"], oy + dr["y"]
                if dr["kind"] == "hp":
                    Color(0.15, 0.85, 0.40, 1)
                else:
                    Color(0.98, 0.78, 0.15, 1)
                Rectangle(pos=(dx - 12, dy - 12), size=(24, 24))
                Color(1, 1, 1, 0.9)
                Line(rectangle=(dx - 12, dy - 12, 24, 24), width=1.5)

            for b in st["bullets"]:
                col = PLAYER_COLORS[b["owner"] % len(PLAYER_COLORS)]
                Color(*col)
                Ellipse(pos=(ox + b["x"] - 6, oy + b["y"] - 6), size=(12, 12))

            for e in st["enemies"]:
                ex, ey = ox + e["x"], oy + e["y"]
                Color(0.94, 0.25, 0.25, 1)
                Ellipse(pos=(ex - 17, ey - 17), size=(34, 34))
                Color(0.25, 0.05, 0.05, 1)
                Rectangle(pos=(ex - 16, ey + 21), size=(32, 5))
                Color(0.13, 0.80, 0.38, 1)
                bar_w = 32 * max(0.0, e["hp"] / max(1, e["max_hp"]))
                Rectangle(pos=(ex - 16, ey + 21), size=(bar_w, 5))

            for idx, p in enumerate(st["players"]):
                if not p["active"]:
                    continue
                px, py = ox + p["x"], oy + p["y"]
                col = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
                if p["hp"] > 0:
                    Color(*col)
                    Ellipse(pos=(px - 19, py - 19), size=(38, 38))
                    if p["boost"] > 0:
                        Color(0.98, 0.85, 0.2, 0.9)
                        Line(circle=(px, py, 24), width=2)
                    else:
                        Color(1, 1, 1, 0.8)
                        Line(circle=(px, py, 19), width=1.5)
                elif p["downed"]:
                    Color(col[0], col[1], col[2], 0.45)
                    Ellipse(pos=(px - 19, py - 19), size=(38, 38))
                    Color(0.98, 0.75, 0.1, 0.9)
                    Line(circle=(px, py, 26), width=2)
                    if p["revive_prog"] > 0:
                        Color(0.13, 0.85, 0.4, 1)
                        angle = 360.0 * min(1.0, p["revive_prog"] / 3.0)
                        Line(circle=(px, py, 30, 0, angle), width=3)
                else:
                    Color(col[0], col[1], col[2], 0.2)
                    Ellipse(pos=(px - 18, py - 18), size=(36, 36))

            (mx, my), (sx, sy), rad = self.get_joy_centers()
            Color(1, 1, 1, 0.16)
            Ellipse(pos=(mx - rad, my - rad), size=(rad * 2, rad * 2))
            Color(1, 1, 1, 0.4)
            Line(circle=(mx, my, rad), width=1.5)
            kx, ky = self.move_knob if self.move_touch_id is not None else (mx, my)
            Color(0.3, 0.7, 1.0, 0.65)
            Ellipse(pos=(kx - rad * 0.42, ky - rad * 0.42), size=(rad * 0.84, rad * 0.84))

            Color(1, 0.35, 0.35, 0.16)
            Ellipse(pos=(sx - rad, sy - rad), size=(rad * 2, rad * 2))
            Color(1, 0.45, 0.45, 0.4)
            Line(circle=(sx, sy, rad), width=1.5)
            skx, sky = self.shoot_knob if self.shoot_touch_id is not None else (sx, sy)
            Color(0.98, 0.4, 0.35, 0.7)
            Ellipse(pos=(skx - rad * 0.42, sky - rad * 0.42), size=(rad * 0.84, rad * 0.84))
class ControlEditorCanvas(Widget):
    def __init__(self, profile_ref, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile_ref
        self.dragging = None
        self.bind(pos=self.redraw, size=self.redraw)

    def get_centers(self):
        ctrl = self.profile["controls"]
        mx = self.x + self.width * ctrl["move_rx"]
        my = self.y + self.height * ctrl["move_ry"]
        sx = self.x + self.width * ctrl["shoot_rx"]
        sy = self.y + self.height * ctrl["shoot_ry"]
        return (mx, my), (sx, sy), ctrl["joy_radius"]

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        (mx, my), (sx, sy), rad = self.get_centers()
        if math.hypot(touch.x - mx, touch.y - my) <= rad * 1.4:
            self.dragging = "move"
            return True
        if math.hypot(touch.x - sx, touch.y - sy) <= rad * 1.4:
            self.dragging = "shoot"
            return True
        return True

    def on_touch_move(self, touch):
        if not self.dragging or self.width == 0 or self.height == 0:
            return True
        rx = max(0.08, min(0.92, (touch.x - self.x) / self.width))
        ry = max(0.10, min(0.85, (touch.y - self.y) / self.height))
        if self.dragging == "move":
            self.profile["controls"]["move_rx"] = round(rx, 3)
            self.profile["controls"]["move_ry"] = round(ry, 3)
        else:
            self.profile["controls"]["shoot_rx"] = round(rx, 3)
            self.profile["controls"]["shoot_ry"] = round(ry, 3)
        self.redraw()
        return True

    def on_touch_up(self, touch):
        self.dragging = None
        return True

    def redraw(self, *args):
        self.canvas.clear()
        (mx, my), (sx, sy), rad = self.get_centers()
        with self.canvas:
            Color(0.12, 0.14, 0.17, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.25, 0.30, 0.35, 1)
            Line(rectangle=(self.x + 2, self.y + 2, self.width - 4, self.height - 4), width=1.5)

            Color(0.3, 0.7, 1.0, 0.35)
            Ellipse(pos=(mx - rad, my - rad), size=(rad * 2, rad * 2))
            Color(0.3, 0.7, 1.0, 0.9)
            Line(circle=(mx, my, rad), width=2)

            Color(0.98, 0.4, 0.35, 0.35)
            Ellipse(pos=(sx - rad, sy - rad), size=(rad * 2, rad * 2))
            Color(0.98, 0.4, 0.35, 0.9)
            Line(circle=(sx, sy, rad), width=2)
class ShooterApp(App):
    def build(self):
        self.profile = load_profile()
        self.net = {
            "role": None,
            "srv": None,
            "udp_running": False,
            "clients": [],
            "conn": None,
            "running": False,
            "room_code": "",
            "my_slot": 0,
            "mlock": None,
            "joining": False,
            "join_id": 0
        }
        self.difficulty = "classic"
        self.match_active = False
        self.paused = False
        self.countdown = 0.0
        self.spawn_timer = 0.0
        self.drop_timer = 0.0
        self.manual_aim = (False, 0.0, 0.0)
        self.shoot_cooldown = 0.0
        self.reset_state(solo=True)

        self.root_box = BoxLayout(orientation="vertical")
        if not self.profile.get("nickname"):
            self.show_register_screen()
        else:
            self.show_main_menu()

        Clock.schedule_interval(self.update_loop, 1.0 / 30.0)
        return self.root_box

    def make_player(self, slot, active=False, nick=""):
        starts = [(0.5, 0.5), (0.35, 0.5), (0.65, 0.5), (0.5, 0.35), (0.5, 0.65)]
        sx, sy = starts[slot % len(starts)]
        return {
            "slot": slot,
            "active": active,
            "nick": nick,
            "x": 300.0 * sx,
            "y": 450.0 * sy,
            "vx": 0.0,
            "vy": 0.0,
            "hp": 100 if active else 0,
            "downed": False,
            "down_timer": 0.0,
            "revive_prog": 0.0,
            "revived": False,
            "boost": 0.0,
            "ready": False
        }

    def reset_state(self, solo=False):
        players = [self.make_player(i, active=(i == 0), nick=(self.profile["nickname"] if i == 0 else "")) for i in range(5)]
        self.state = {
            "players": players,
            "bullets": [],
            "enemies": [],
            "drops": [],
            "obstacles": [
                {"rx": 0.16, "ry": 0.68, "rw": 0.15, "rh": 0.10},
                {"rx": 0.69, "ry": 0.68, "rw": 0.15, "rh": 0.10},
                {"rx": 0.16, "ry": 0.22, "rw": 0.15, "rh": 0.10},
                {"rx": 0.69, "ry": 0.22, "rw": 0.15, "rh": 0.10},
                {"rx": 0.41, "ry": 0.46, "rw": 0.18, "rh": 0.08}
            ],
            "score": 0,
            "kills": 0,
            "wave": 1,
            "started": False,
            "countdown": 0.0,
            "game_over": False
        }

    def show_register_screen(self):
        self.root_box.clear_widgets()
        box = BoxLayout(orientation="vertical", padding=28, spacing=16)
        box.add_widget(Widget(size_hint=(1, 0.15)))

        lbl = Label(
            text="РЕГИСТРАЦИЯ БОЙЦА",
            font_size="24sp",
            bold=True,
            color=(0, 1, 0.53, 1),
            size_hint=(1, 0.15)
        )
        box.add_widget(lbl)

        sub = Label(
            text="Придумай никнейм для профиля и будущего рейтинга:",
            font_size="14sp",
            color=(0.8, 0.8, 0.8, 1),
            size_hint=(1, 0.1)
        )
        box.add_widget(sub)

        self.nick_input = TextInput(
            text=self.profile.get("nickname", ""),
            hint_text="Введи ник...",
            multiline=False,
            halign="center",
            font_size="20sp",
            size_hint=(1, 0.14)
        )
        box.add_widget(self.nick_input)

        btn_save = Button(
            text="СОХРАНИТЬ И ВОЙТИ",
            font_size="17sp",
            bold=True,
            background_color=(0.09, 0.64, 0.29, 1),
            size_hint=(1, 0.15)
        )
        btn_save.bind(on_press=self.confirm_nickname)
        box.add_widget(btn_save)
        box.add_widget(Widget(size_hint=(1, 0.31)))
        self.root_box.add_widget(box)

    def confirm_nickname(self, instance):
        nick = self.nick_input.text.strip()
        if not nick:
            nick = f"Player{random.randint(100, 999)}"
        self.profile["nickname"] = nick[:16]
        save_profile(self.profile)
        self.show_main_menu()
    def show_main_menu(self, instance=None):
        self.close_network()
        self.match_active = False
        self.paused = False
        self.root_box.clear_widgets()

        menu = BoxLayout(orientation="vertical", padding=18, spacing=10)

        top_bar = BoxLayout(orientation="horizontal", size_hint=(1, 0.11), spacing=8)
        prof_lbl = Label(
            text=f"{self.profile['nickname']}  |  Рекорд: {self.profile['best_score']}  |  Монеты: {self.profile['coins']}",
            font_size="14sp",
            bold=True,
            color=(0.98, 0.85, 0.2, 1)
        )
        btn_nick = Button(text="НИК", size_hint=(0.20, 1), background_color=(0.25, 0.28, 0.35, 1), bold=True)
        btn_nick.bind(on_press=lambda x: self.show_register_screen())
        top_bar.add_widget(prof_lbl)
        top_bar.add_widget(btn_nick)
        menu.add_widget(top_bar)

        title = Label(
            text="ARENA SHOOTER",
            font_size="26sp",
            bold=True,
            color=(0, 1, 0.53, 1),
            size_hint=(1, 0.16)
        )
        menu.add_widget(title)

        btn_solo = Button(
            text="ОДИНОЧНАЯ ИГРА",
            font_size="16sp",
            bold=True,
            background_color=(0.15, 0.39, 0.92, 1),
            size_hint=(1, 0.14)
        )
        btn_solo.bind(on_press=lambda x: self.show_difficulty_select("solo"))
        menu.add_widget(btn_solo)

        btn_lan = Button(
            text="ЛОКАЛЬНАЯ СЕТЬ (ДО 5 ИГРОКОВ)",
            font_size="16sp",
            bold=True,
            background_color=(0.09, 0.64, 0.29, 1),
            size_hint=(1, 0.14)
        )
        btn_lan.bind(on_press=lambda x: self.show_difficulty_select("lan"))
        menu.add_widget(btn_lan)

        btn_ctrl = Button(
            text="НАСТРОЙКИ УПРАВЛЕНИЯ",
            font_size="16sp",
            bold=True,
            background_color=(0.35, 0.38, 0.46, 1),
            size_hint=(1, 0.14)
        )
        btn_ctrl.bind(on_press=self.open_controls_settings)
        menu.add_widget(btn_ctrl)

        btn_online = Button(
            text="ОНЛАЙН И РЕЙТИНГ (В РАЗРАБОТКЕ)",
            font_size="14sp",
            bold=True,
            background_color=(0.65, 0.38, 0.05, 1),
            size_hint=(1, 0.12)
        )
        btn_online.bind(on_press=self.open_online_lobby)
        menu.add_widget(btn_online)

        menu.add_widget(Widget(size_hint=(1, 0.19)))
        self.root_box.add_widget(menu)

    def show_difficulty_select(self, mode):
        self.root_box.clear_widgets()
        box = BoxLayout(orientation="vertical", padding=24, spacing=14)
        box.add_widget(Widget(size_hint=(1, 0.12)))

        lbl = Label(
            text="ВЫБЕРИ СЛОЖНОСТЬ",
            font_size="22sp",
            bold=True,
            color=(0, 1, 0.53, 1),
            size_hint=(1, 0.14)
        )
        box.add_widget(lbl)

        btn_classic = Button(
            text="КЛАССИЧЕСКАЯ\n(Стандартный темп и урон врагов)",
            halign="center",
            font_size="15sp",
            bold=True,
            background_color=(0.15, 0.39, 0.92, 1),
            size_hint=(1, 0.20)
        )
        btn_classic.bind(on_press=lambda x: self.proceed_after_diff(mode, "classic"))
        box.add_widget(btn_classic)

        btn_medium = Button(
            text="СРЕДНЯЯ\n(Враги быстрее +35%, повышенный урон)",
            halign="center",
            font_size="15sp",
            bold=True,
            background_color=(0.82, 0.35, 0.12, 1),
            size_hint=(1, 0.20)
        )
        btn_medium.bind(on_press=lambda x: self.proceed_after_diff(mode, "medium"))
        box.add_widget(btn_medium)

        btn_back = Button(
            text="НАЗАД В МЕНЮ",
            font_size="15sp",
            bold=True,
            background_color=(0.5, 0.15, 0.15, 1),
            size_hint=(1, 0.14)
        )
        btn_back.bind(on_press=self.show_main_menu)
        box.add_widget(btn_back)
        box.add_widget(Widget(size_hint=(1, 0.20)))
        self.root_box.add_widget(box)

    def proceed_after_diff(self, mode, diff):
        self.difficulty = diff
        if mode == "solo":
            self.start_solo_mode()
        else:
            self.open_lan_lobby()

    def open_controls_settings(self, instance):
        self.root_box.clear_widgets()
        box = BoxLayout(orientation="vertical", padding=10, spacing=8)

        hint = Label(
            text="ПЕРЕТАЩИ ДЖОЙСТИКИ ПАЛЬЦЕМ (Синий: ход, Красный: огонь)",
            size_hint=(1, 0.08),
            font_size="13sp",
            bold=True,
            color=(0, 1, 0.53, 1)
        )
        box.add_widget(hint)

        self.ctrl_editor = ControlEditorCanvas(self.profile, size_hint=(1, 0.72))
        box.add_widget(self.ctrl_editor)

        row1 = BoxLayout(orientation="horizontal", size_hint=(1, 0.10), spacing=6)
        btn_minus = Button(text="РАЗМЕР -", bold=True, background_color=(0.25, 0.28, 0.35, 1))
        btn_minus.bind(on_press=lambda x: self.change_joy_size(-6))
        btn_plus = Button(text="РАЗМЕР +", bold=True, background_color=(0.25, 0.28, 0.35, 1))
        btn_plus.bind(on_press=lambda x: self.change_joy_size(6))

        auto_txt = "АВТО-ОГОНЬ: ВКЛ" if self.profile["controls"]["auto_fire"] else "АВТО-ОГОНЬ: ВЫКЛ"
        self.btn_auto = Button(text=auto_txt, bold=True, background_color=(0.15, 0.45, 0.75, 1))
        self.btn_auto.bind(on_press=self.toggle_auto_fire)

        row1.add_widget(btn_minus)
        row1.add_widget(btn_plus)
        row1.add_widget(self.btn_auto)
        box.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", size_hint=(1, 0.10), spacing=6)
        btn_reset = Button(text="СБРОС", bold=True, background_color=(0.55, 0.25, 0.15, 1))
        btn_reset.bind(on_press=self.reset_controls_default)
        btn_save = Button(text="СОХРАНИТЬ И В МЕНЮ", bold=True, background_color=(0.09, 0.64, 0.29, 1))
        btn_save.bind(on_press=self.save_controls_and_exit)

        row2.add_widget(btn_reset)
        row2.add_widget(btn_save)
        box.add_widget(row2)
        self.root_box.add_widget(box)

    def change_joy_size(self, delta):
        rad = self.profile["controls"]["joy_radius"] + delta
        self.profile["controls"]["joy_radius"] = max(34.0, min(82.0, rad))
        self.ctrl_editor.redraw()

    def toggle_auto_fire(self, instance):
        curr = not self.profile["controls"]["auto_fire"]
        self.profile["controls"]["auto_fire"] = curr
        self.btn_auto.text = "АВТО-ОГОНЬ: ВКЛ" if curr else "АВТО-ОГОНЬ: ВЫКЛ"

    def reset_controls_default(self, instance):
        self.profile["controls"].update({
            "move_rx": 0.16,
            "move_ry": 0.22,
            "shoot_rx": 0.84,
            "shoot_ry": 0.22,
            "joy_radius": 52.0,
            "auto_fire": True
        })
        self.btn_auto.text = "АВТО-ОГОНЬ: ВКЛ"
        self.ctrl_editor.redraw()

    def save_controls_and_exit(self, instance):
        save_profile(self.profile)
        self.show_main_menu()
    def open_online_lobby(self, instance):
        self.root_box.clear_widgets()
        box = BoxLayout(orientation="vertical", padding=24, spacing=16)
        box.add_widget(Widget(size_hint=(1, 0.2)))

        status_lbl = Label(
            text="МИРОВОЙ РЕЙТИНГ И ОНЛАЙН",
            font_size="20sp",
            bold=True,
            color=(0.98, 0.8, 0.08, 1),
            size_hint=(1, 0.15)
        )
        box.add_widget(status_lbl)

        sub_lbl = Label(
            text=f"Твой профиль: {self.profile['nickname']}\nЛучший рекорд: {self.profile['best_score']} | Монеты: {self.profile['coins']}\nСервер рейтинга и сезонов готовится к запуску.",
            font_size="14sp",
            halign="center",
            color=(0.8, 0.8, 0.8, 1),
            size_hint=(1, 0.25)
        )
        box.add_widget(sub_lbl)

        btn_back = Button(
            text="ВЕРНУТЬСЯ В МЕНЮ",
            font_size="16sp",
            bold=True,
            background_color=(0.6, 0.15, 0.15, 1),
            size_hint=(1, 0.15)
        )
        btn_back.bind(on_press=self.show_main_menu)
        box.add_widget(btn_back)
        box.add_widget(Widget(size_hint=(1, 0.25)))
        self.root_box.add_widget(box)

    def build_arena_screen(self, show_room_code=False):
        self.root_box.clear_widgets()

        top_spacer = Widget(size_hint=(1, None), height="36dp")
        self.root_box.add_widget(top_spacer)

        self.top_hud_box = BoxLayout(orientation="horizontal", size_hint=(1, None), height="42dp", spacing=4)

        self.hp_lbl = Label(text="HP: 100", font_size="14sp", bold=True, color=(0.15, 0.9, 0.45, 1), size_hint=(0.28, 1))
        self.wave_lbl = Label(text="ВОЛНА 1", font_size="14sp", bold=True, color=(0.98, 0.85, 0.2, 1), size_hint=(0.28, 1))
        self.score_lbl = Label(text="СЧЁТ: 0", font_size="14sp", bold=True, color=(0.4, 0.75, 1.0, 1), size_hint=(0.30, 1))

        self.btn_pause = Button(text="II", font_size="14sp", bold=True, background_color=(0.28, 0.32, 0.38, 1), size_hint=(0.14, 1))
        self.btn_pause.bind(on_press=self.toggle_pause)

        self.top_hud_box.add_widget(self.hp_lbl)
        self.top_hud_box.add_widget(self.wave_lbl)
        self.top_hud_box.add_widget(self.score_lbl)
        self.top_hud_box.add_widget(self.btn_pause)
        self.root_box.add_widget(self.top_hud_box)

        self.code_badge = Label(
            text="",
            size_hint=(1, None),
            height="28dp" if show_room_code else "1dp",
            opacity=1.0 if show_room_code else 0.0,
            bold=True,
            font_size="15sp",
            color=(0.98, 0.82, 0.15, 1)
        )
        self.root_box.add_widget(self.code_badge)

        self.info_lbl = Label(
            text="",
            size_hint=(1, None),
            height="48dp" if show_room_code else "1dp",
            opacity=1.0 if show_room_code else 0.0,
            font_size="12sp",
            halign="center",
            valign="middle",
            color=(0.75, 0.8, 0.88, 1)
        )
        self.info_lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.root_box.add_widget(self.info_lbl)

        self.arena_wrap = FloatLayout(size_hint=(1, 1))
        self.arena = ArenaCanvas(self, size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self.arena_wrap.add_widget(self.arena)

        self.center_banner = Label(
            text="",
            font_size="26sp",
            bold=True,
            color=(0, 1, 0.53, 1),
            size_hint=(0.85, 0.25),
            pos_hint={"center_x": 0.5, "center_y": 0.58}
        )
        self.arena_wrap.add_widget(self.center_banner)
        self.root_box.add_widget(self.arena_wrap)

        self.bottom_box = BoxLayout(orientation="vertical", size_hint=(1, None), height="68dp", padding=6, spacing=6)
        self.root_box.add_widget(self.bottom_box)

    def hide_room_code_badge(self):
        if hasattr(self, "code_badge"):
            self.code_badge.text = ""
            self.code_badge.opacity = 0.0
            self.code_badge.height = "1dp"
        if hasattr(self, "info_lbl"):
            self.info_lbl.text = ""
            self.info_lbl.opacity = 0.0
            self.info_lbl.height = "1dp"

    def toggle_pause(self, instance):
        if self.net["role"] != "solo" or not self.match_active or self.state["game_over"]:
            return
        self.paused = not self.paused
        self.center_banner.text = "ПАУЗА" if self.paused else ""
        self.btn_pause.text = ">" if self.paused else "II"

    def start_solo_mode(self):
        self.reset_state(solo=True)
        self.net["role"] = "solo"
        self.net["my_slot"] = 0
        self.net["running"] = True
        self.match_active = False
        self.paused = False
        self.build_arena_screen(show_room_code=False)
        self.center_banner.text = "ПРИГОТОВЬСЯ К БОЮ"
        self.setup_ready_footer(is_solo=True)

    def setup_ready_footer(self, is_solo=True):
        self.bottom_box.clear_widgets()
        row = BoxLayout(orientation="horizontal", spacing=6)
        btn_ready = Button(text="ГОТОВ К БОЮ!", background_color=(0.09, 0.64, 0.29, 1), bold=True, font_size="16sp")
        if is_solo:
            btn_ready.bind(on_press=self.begin_countdown)
        else:
            btn_ready.bind(on_press=self.mark_lan_ready)

        btn_menu = Button(text="В МЕНЮ", background_color=(0.55, 0.15, 0.15, 1), bold=True, size_hint=(0.35, 1))
        btn_menu.bind(on_press=self.show_main_menu)
        row.add_widget(btn_ready)
        row.add_widget(btn_menu)
        self.bottom_box.add_widget(row)

    def begin_countdown(self, instance=None):
        self.hide_room_code_badge()
        self.state["countdown"] = 3.0
        self.state["started"] = False
        self.match_active = True
        self.bottom_box.clear_widgets()
        row = BoxLayout(orientation="horizontal", spacing=6)
        btn_m = Button(text="ВЫЙТИ В МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True)
        btn_m.bind(on_press=self.show_main_menu)
        row.add_widget(btn_m)
        self.bottom_box.add_widget(row)

    def open_lan_lobby(self):
        self.reset_state(solo=False)
        self.match_active = False
        self.build_arena_screen(show_room_code=True)
        self.code_badge.text = "СОЗДАЙ КОМНАТУ ИЛИ ВВЕДИ 4-ЗНАЧНЫЙ КОД"

        self.code_input = TextInput(
            text="",
            hint_text="Код комнаты (4 цифры) или IP",
            multiline=False,
            halign="center",
            font_size="18sp",
            input_filter=only_digits_and_dots
        )
        self.bottom_box.add_widget(self.code_input)

        row = BoxLayout(orientation="horizontal", spacing=6)
        btn_host = Button(text="СОЗДАТЬ", background_color=(0.15, 0.39, 0.92, 1), bold=True)
        btn_host.bind(on_press=self.start_host)
        btn_join = Button(text="ВОЙТИ ПО КОДУ", background_color=(0.09, 0.64, 0.29, 1), bold=True)
        btn_join.bind(on_press=self.start_client_by_code)
        btn_back = Button(text="МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True, size_hint=(0.28, 1))
        btn_back.bind(on_press=self.show_main_menu)

        row.add_widget(btn_host)
        row.add_widget(btn_join)
        row.add_widget(btn_back)
        self.bottom_box.add_widget(row)
    def close_network(self):
        self.net["join_id"] = self.net.get("join_id", 0) + 1
        self.net["joining"] = False
        self.release_multicast_lock()
        self.net["running"] = False
        self.net["udp_running"] = False
        if self.net["conn"]:
            try:
                self.net["conn"].close()
            except Exception:
                pass
            self.net["conn"] = None
        for c in self.net["clients"]:
            try:
                c["sock"].close()
            except Exception:
                pass
        self.net["clients"] = []
        if self.net["srv"]:
            try:
                self.net["srv"].close()
            except Exception:
                pass
            self.net["srv"] = None
        self.net["role"] = None

    def set_move_dir(self, vx, vy):
        slot = self.net["my_slot"]
        if self.net["role"] in ["host", "solo"]:
            self.state["players"][slot]["vx"] = vx
            self.state["players"][slot]["vy"] = vy
        elif self.net["role"] == "client" and self.net["conn"]:
            self.send_to_host({"type": "move", "vx": vx, "vy": vy})

    def set_manual_aim(self, active, ax, ay):
        self.manual_aim = (active, ax, ay)
        if self.net["role"] == "client" and self.net["conn"]:
            self.send_to_host({"type": "aim", "active": active, "ax": ax, "ay": ay})

    def send_to_host(self, payload):
        if self.net["conn"]:
            try:
                self.net["conn"].sendall((json.dumps(payload) + "\n").encode("utf-8"))
            except Exception:
                pass

    def acquire_multicast_lock(self):
        """Android иногда режет широковещательные пакеты: просим систему их пропускать."""
        if self.net.get("mlock"):
            return
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")
            wifi = PythonActivity.mActivity.getSystemService(Context.WIFI_SERVICE)
            lock = wifi.createMulticastLock("arena_shooter")
            lock.setReferenceCounted(False)
            lock.acquire()
            self.net["mlock"] = lock
        except Exception:
            pass

    def release_multicast_lock(self):
        lock = self.net.get("mlock")
        self.net["mlock"] = None
        if lock:
            try:
                lock.release()
            except Exception:
                pass

    def set_info(self, text):
        """Подсказка под кодом комнаты. Можно вызывать из любого потока."""
        def _apply(dt):
            if hasattr(self, "info_lbl"):
                self.info_lbl.text = text
        Clock.schedule_once(_apply, 0)

    def net_fail(self, badge_text, info_text=""):
        """Показываем, что пошло не так, и сбрасываем попытку подключения."""
        def _apply(dt):
            if self.net["role"] == "client":
                self.net["role"] = None
                self.release_multicast_lock()
            if hasattr(self, "code_badge"):
                self.code_badge.text = badge_text
            if hasattr(self, "info_lbl"):
                self.info_lbl.text = info_text
        Clock.schedule_once(_apply, 0)

    def start_host(self, instance):
        if self.net["role"] == "host" or self.net.get("joining"):
            return
        self.close_network()
        code = str(random.randint(1000, 9999))
        self.net["role"] = "host"
        self.net["my_slot"] = 0
        self.net["room_code"] = code
        self.net["running"] = True
        self.net["udp_running"] = True

        ips, _ = get_network_info()
        ip_txt = ", ".join(ips) if ips else "не найден (включи Wi-Fi или точку доступа)"
        self.code_badge.text = f"КОД КОМНАТЫ: {code}  |  ИГРОКОВ: 1/5"
        self.info_lbl.text = f"Если друг не видит комнату, пусть введёт вместо кода IP: {ip_txt}"
        self.center_banner.text = "ОЖИДАНИЕ ДРУЗЕЙ..."
        self.setup_ready_footer(is_solo=False)

        threading.Thread(target=self.udp_beacon_worker, args=(code,), daemon=True).start()
        threading.Thread(target=self.tcp_host_worker, daemon=True).start()

    def udp_beacon_worker(self, code):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception as e:
            self.set_info("Ошибка сети (UDP): " + describe_net_error(e))
            return
        msg = f"ARENA_ROOM:{code}".encode("utf-8")
        targets = []
        tick = 0
        while self.net["udp_running"] and self.net["room_code"] == code and not self.state["started"]:
            if tick % 3 == 0:
                _, targets = get_network_info()
            tick += 1
            for t in targets:
                try:
                    sock.sendto(msg, (t, UDP_PORT))
                except Exception:
                    pass
            threading.Event().wait(1.0)
        sock.close()

    def tcp_host_worker(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", TCP_PORT))
            srv.listen(4)
        except Exception as e:
            self.set_info("Не удалось открыть комнату: " + describe_net_error(e))
            return
        self.net["srv"] = srv
        if not self.net["running"]:
            srv.close()
            return

        while self.net["running"] and not self.state["started"]:
            try:
                conn, addr = srv.accept()
            except Exception:
                break
            free_slot, client_info = -1, None
            try:
                for i in range(1, 5):
                    if not self.state["players"][i]["active"]:
                        free_slot = i
                        break
                if free_slot == -1:
                    conn.close()
                    continue

                self.state["players"][free_slot] = self.make_player(free_slot, active=True, nick=f"Игрок {free_slot + 1}")
                client_info = {"sock": conn, "slot": free_slot, "aim": (False, 0.0, 0.0)}
                self.net["clients"].append(client_info)

                conn.sendall((json.dumps({"type": "welcome", "slot": free_slot}) + "\n").encode("utf-8"))
                Clock.schedule_once(lambda dt: self.update_lobby_counter(), 0)
                threading.Thread(target=self.handle_client_conn, args=(client_info,), daemon=True).start()
            except Exception:
                if client_info in self.net["clients"]:
                    self.net["clients"].remove(client_info)
                if free_slot != -1:
                    self.state["players"][free_slot]["active"] = False
                    self.state["players"][free_slot]["hp"] = 0
                try:
                    conn.close()
                except Exception:
                    pass

    def update_lobby_counter(self):
        cnt = sum(1 for p in self.state["players"] if p["active"])
        if self.code_badge.opacity > 0:
            self.code_badge.text = f"КОД КОМНАТЫ: {self.net['room_code']}  |  ИГРОКОВ: {cnt}/5"

    def handle_client_conn(self, cinfo):
        conn = cinfo["sock"]
        slot = cinfo["slot"]
        buf = ""
        while self.net["running"]:
            try:
                data = conn.recv(4096).decode("utf-8")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    msg = json.loads(line)
                    mtype = msg.get("type")
                    if mtype == "move":
                        self.state["players"][slot]["vx"] = msg["vx"]
                        self.state["players"][slot]["vy"] = msg["vy"]
                    elif mtype == "aim":
                        cinfo["aim"] = (msg["active"], msg["ax"], msg["ay"])
                    elif mtype == "nick":
                        self.state["players"][slot]["nick"] = msg["nick"][:16]
                    elif mtype == "ready":
                        self.state["players"][slot]["ready"] = True
                        Clock.schedule_once(lambda dt: self.check_all_lan_ready(), 0)
            except Exception:
                break
        self.state["players"][slot]["active"] = False
        self.state["players"][slot]["hp"] = 0
        Clock.schedule_once(lambda dt: self.update_lobby_counter(), 0)

    def start_client_by_code(self, instance):
        if self.net.get("joining") or self.net["role"] == "host":
            return
        value = self.code_input.text.strip()
        by_ip = is_ip_address(value)
        if not by_ip and not (len(value) == 4 and value.isdigit()):
            self.code_badge.text = "ВВЕДИ 4 ЦИФРЫ КОДА ИЛИ IP ХОСТА!"
            return
        self.net["join_id"] = self.net.get("join_id", 0) + 1
        jid = self.net["join_id"]
        self.net["joining"] = True
        self.net["role"] = "client"
        self.acquire_multicast_lock()
        self.code_badge.text = f"ПОДКЛЮЧЕНИЕ К {value}..." if by_ip else f"ПОИСК КОМНАТЫ {value}..."
        self.info_lbl.text = ""
        threading.Thread(target=self.client_discover_and_connect, args=(value, jid), daemon=True).start()

    def discover_host(self, code, timeout=8.0):
        """Ждём от хоста сообщение ARENA_ROOM:<код>. Возвращает (ip, текст_ошибки)."""
        expected = f"ARENA_ROOM:{code}"
        udp = None
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("", UDP_PORT))
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, None
                udp.settimeout(left)
                data, addr = udp.recvfrom(1024)
                if data.decode("utf-8", errors="ignore").strip() == expected:
                    return addr[0], None
        except socket.timeout:
            return None, None
        except Exception as e:
            return None, describe_net_error(e)
        finally:
            if udp is not None:
                try:
                    udp.close()
                except Exception:
                    pass

    def client_discover_and_connect(self, target, jid):
        try:
            if is_ip_address(target):
                host_ip = target
            else:
                host_ip, err = self.discover_host(target)
                if jid != self.net.get("join_id"):
                    return
                if not host_ip:
                    if err:
                        self.net_fail("ОШИБКА СЕТИ", err)
                    else:
                        self.net_fail(
                            "КОМНАТА НЕ НАЙДЕНА!",
                            "Проверь код. Телефоны должны быть в одной сети (Wi-Fi или точка доступа). "
                            "Можно ввести IP хоста вместо кода."
                        )
                    return

            cli = None
            try:
                cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                cli.settimeout(5.0)
                cli.connect((host_ip, TCP_PORT))
                cli.settimeout(None)
            except Exception as e:
                if cli is not None:
                    try:
                        cli.close()
                    except Exception:
                        pass
                self.net_fail("ОШИБКА ПОДКЛЮЧЕНИЯ К ХОСТУ", f"{host_ip}: {describe_net_error(e)}")
                return

            if jid != self.net.get("join_id"):
                cli.close()
                return

            self.net["conn"] = cli
            self.net["running"] = True
            self.send_to_host({"type": "nick", "nick": self.profile["nickname"]})
            Clock.schedule_once(
                lambda dt: self.on_client_joined_lobby(target) if jid == self.net.get("join_id") else None, 0
            )

            buf = ""
            while self.net["running"]:
                try:
                    data = cli.recv(16384).decode("utf-8")
                    if not data:
                        break
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        msg = json.loads(line)
                        if msg.get("type") == "welcome":
                            self.net["my_slot"] = msg["slot"]
                        elif msg.get("type") == "state":
                            was_started = self.state.get("started", False)
                            self.state.update(msg["data"])
                            if (self.state["started"] or self.state["countdown"] > 0) and not was_started:
                                Clock.schedule_once(lambda dt: self.on_client_match_started(), 0)
                except Exception:
                    break
        finally:
            if jid == self.net.get("join_id"):
                self.net["joining"] = False

    def on_client_joined_lobby(self, code):
        self.release_multicast_lock()
        self.code_badge.text = f"ПОДКЛЮЧЕНО К КОМНАТЕ {code}"
        self.info_lbl.text = ""
        self.center_banner.text = "НАЖМИ ГОТОВ!"
        self.setup_ready_footer(is_solo=False)

    def on_client_match_started(self):
        self.hide_room_code_badge()
        self.match_active = True
        self.bottom_box.clear_widgets()
        btn_m = Button(text="ВЫЙТИ В МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True)
        btn_m.bind(on_press=self.show_main_menu)
        self.bottom_box.add_widget(btn_m)

    def mark_lan_ready(self, instance):
        instance.disabled = True
        instance.text = "ОЖИДАНИЕ ОСТАЛЬНЫХ..."
        if self.net["role"] == "host":
            self.state["players"][0]["ready"] = True
            self.check_all_lan_ready()
        else:
            self.send_to_host({"type": "ready"})

    def check_all_lan_ready(self):
        active_players = [p for p in self.state["players"] if p["active"]]
        if len(active_players) >= 1 and all(p["ready"] for p in active_players):
            self.net["udp_running"] = False
            self.begin_countdown()
    def point_in_obstacle(self, x, y, r, w, h):
        for obs in self.state["obstacles"]:
            ox1 = obs["rx"] * w - r
            ox2 = (obs["rx"] + obs["rw"]) * w + r
            oy1 = obs["ry"] * h - r
            oy2 = (obs["ry"] + obs["rh"]) * h + r
            in_x = (x >= ox1) and (ox2 >= x)
            in_y = (y >= oy1) and (oy2 >= y)
            if in_x and in_y:
                return True
        return False

    def move_entity_safely(self, entity, dx, dy, radius, w, h):
        nx = max(22.0, min(w - 22.0, entity["x"] + dx))
        if not self.point_in_obstacle(nx, entity["y"], radius, w, h):
            entity["x"] = nx

        ny = max(22.0, min(h - 22.0, entity["y"] + dy))
        if not self.point_in_obstacle(entity["x"], ny, radius, w, h):
            entity["y"] = ny

    def spawn_bullet_dir(self, px, py, dx, dy, owner):
        dist = math.hypot(dx, dy)
        if dist == 0:
            return
        speed = 17.0
        self.state["bullets"].append({
            "x": px,
            "y": py,
            "vx": (dx / dist) * speed,
            "vy": (dy / dist) * speed,
            "owner": owner
        })

    def process_player_shooting(self, dt, w, h):
        self.shoot_cooldown = max(0.0, self.shoot_cooldown - dt)
        if self.shoot_cooldown > 0:
            return

        auto_on = self.profile["controls"]["auto_fire"]
        fired = False

        for slot, p in enumerate(self.state["players"]):
            if not p["active"] or p["hp"] <= 0:
                continue

            aim_active, ax, ay = (False, 0.0, 0.0)
            if slot == self.net["my_slot"]:
                aim_active, ax, ay = self.manual_aim
            else:
                for c in self.net["clients"]:
                    if c["slot"] == slot:
                        aim_active, ax, ay = c["aim"]
                        break

            if aim_active:
                self.spawn_bullet_dir(p["x"], p["y"], ax, ay, slot)
                fired = True
            elif auto_on and len(self.state["enemies"]) > 0:
                nearest = min(self.state["enemies"], key=lambda e: math.hypot(e["x"] - p["x"], e["y"] - p["y"]))
                dist = math.hypot(nearest["x"] - p["x"], nearest["y"] - p["y"])
                if dist <= max(w, h) * 0.65:
                    self.spawn_bullet_dir(p["x"], p["y"], nearest["x"] - p["x"], nearest["y"] - p["y"], slot)
                    fired = True

        if fired:
            self.shoot_cooldown = 0.24

    def update_physics(self, dt):
        w = max(320.0, self.arena.width)
        h = max(420.0, self.arena.height)
        is_med = (self.difficulty == "medium")

        for p in self.state["players"]:
            if not p["active"]:
                continue
            if p["hp"] > 0:
                p["boost"] = max(0.0, p["boost"] - dt)
                spd = 7.2 if p["boost"] > 0 else 5.2
                self.move_entity_safely(p, p["vx"] * spd, p["vy"] * spd, 19, w, h)
            elif p["downed"]:
                p["down_timer"] -= dt
                if p["down_timer"] <= 0:
                    p["downed"] = False
                    p["revived"] = True
                    p["revive_prog"] = 0.0
                else:
                    rescuer_near = False
                    for ally in self.state["players"]:
                        if ally["active"] and ally["hp"] > 0 and ally["slot"] != p["slot"]:
                            if math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= 44:
                                rescuer_near = True
                                break
                    if rescuer_near:
                        p["revive_prog"] += dt
                        if p["revive_prog"] >= 3.0:
                            p["hp"] = 50
                            p["downed"] = False
                            p["revived"] = True
                            p["revive_prog"] = 0.0
                    else:
                        p["revive_prog"] = max(0.0, p["revive_prog"] - dt * 0.5)

        alive_players = [p for p in self.state["players"] if p["active"] and p["hp"] > 0]
        downed_players = [p for p in self.state["players"] if p["active"] and p["downed"]]
        if len(alive_players) == 0 and len(downed_players) == 0:
            self.finish_match()
            return

        self.process_player_shooting(dt, w, h)

        for b in self.state["bullets"][:]:
            b["x"] += b["vx"]
            b["y"] += b["vy"]
            in_x = (b["x"] >= 0) and (w >= b["x"])
            in_y = (b["y"] >= 0) and (h >= b["y"])
            if (not in_x) or (not in_y) or self.point_in_obstacle(b["x"], b["y"], 4, w, h):
                self.state["bullets"].remove(b)

        self.drop_timer += dt
        if self.drop_timer >= 16.0 and len(self.state["drops"]) < 2:
            self.drop_timer = 0.0
            for _ in range(10):
                dx = random.uniform(40, w - 40)
                dy = random.uniform(40, h - 40)
                if not self.point_in_obstacle(dx, dy, 24, w, h):
                    kind = random.choice(["hp", "boost"])
                    self.state["drops"].append({"x": dx, "y": dy, "kind": kind, "ttl": 15.0})
                    break

        for dr in self.state["drops"][:]:
            dr["ttl"] -= dt
            if dr["ttl"] <= 0:
                self.state["drops"].remove(dr)
                continue
            for p in alive_players:
                if math.hypot(p["x"] - dr["x"], p["y"] - dr["y"]) <= 30:
                    if dr["kind"] == "hp":
                        p["hp"] = min(100, p["hp"] + 25)
                    else:
                        p["boost"] = 5.0
                    if dr in self.state["drops"]:
                        self.state["drops"].remove(dr)
                    break

        self.spawn_timer += dt
        base_delay = 1.8 if is_med else 2.2
        spawn_delay = max(0.6, base_delay - self.state["wave"] * 0.14)
        if self.spawn_timer >= spawn_delay:
            self.spawn_timer = 0.0
            side = random.choice(["top", "bottom", "left", "right"])
            if side == "top":
                ex, ey = random.uniform(25, w - 25), h - 12
            elif side == "bottom":
                ex, ey = random.uniform(25, w - 25), 12
            elif side == "left":
                ex, ey = 12, random.uniform(25, h - 25)
            else:
                ex, ey = w - 12, random.uniform(25, h - 25)
            hp = (3 if is_med else 2) + (self.state["wave"] // 2)
            spd_mult = 1.35 if is_med else 1.0
            spd = min(5.2, (1.5 + self.state["wave"] * 0.18) * spd_mult)
            self.state["enemies"].append({"x": ex, "y": ey, "hp": hp, "max_hp": hp, "speed": spd})

        dmg = 24 if is_med else 15
        for e in self.state["enemies"][:]:
            if len(alive_players) == 0:
                break
            target = min(alive_players, key=lambda t: math.hypot(t["x"] - e["x"], t["y"] - e["y"]))
            edx = target["x"] - e["x"]
            edy = target["y"] - e["y"]
            edist = math.hypot(edx, edy)
            if edist > 0:
                vx = (edx / edist) * e["speed"]
                vy = (edy / edist) * e["speed"]
                old_x, old_y = e["x"], e["y"]
                self.move_entity_safely(e, vx, vy, 16, w, h)
                if abs(e["x"] - old_x) < 0.1 and abs(edx) > 1:
                    sdir = 1 if edx > 0 else -1
                    self.move_entity_safely(e, sdir * e["speed"], 0, 16, w, h)
                elif abs(e["y"] - old_y) < 0.1 and abs(edy) > 1:
                    vdir = 1 if edy > 0 else -1
                    self.move_entity_safely(e, 0, vdir * e["speed"], 16, w, h)

            if math.hypot(target["x"] - e["x"], target["y"] - e["y"]) <= 27:
                target["hp"] = max(0, target["hp"] - dmg)
                if e in self.state["enemies"]:
                    self.state["enemies"].remove(e)
                if target["hp"] == 0:
                    active_cnt = sum(1 for pl in self.state["players"] if pl["active"])
                    if active_cnt > 1 and not target["revived"]:
                        target["downed"] = True
                        target["down_timer"] = 15.0
                        target["revive_prog"] = 0.0
                    else:
                        target["revived"] = True
                continue

            for b in self.state["bullets"][:]:
                if math.hypot(b["x"] - e["x"], b["y"] - e["y"]) <= 22:
                    e["hp"] -= 1
                    if b in self.state["bullets"]:
                        self.state["bullets"].remove(b)
                    if e["hp"] <= 0:
                        if e in self.state["enemies"]:
                            self.state["enemies"].remove(e)
                        self.state["score"] += 10
                        self.state["kills"] += 1
                        new_wave = 1 + (self.state["score"] // 100)
                        if new_wave > self.state["wave"]:
                            self.state["wave"] = new_wave
                            for pl in self.state["players"]:
                                if pl["active"] and pl["downed"]:
                                    pl["down_timer"] += 5.0
                    break

    def finish_match(self):
        if self.state["game_over"]:
            return
        self.state["game_over"] = True
        sc = self.state["score"]
        earned = max(1, sc // 10)
        is_record = sc > self.profile["best_score"]
        if is_record:
            self.profile["best_score"] = sc
        self.profile["coins"] += earned
        self.profile["total_kills"] += self.state["kills"]
        save_profile(self.profile)

        rec_txt = "\nНОВЫЙ РЕКОРД!" if is_record else ""
        self.center_banner.text = f"БОЙ ОКОНЧЕН!{rec_txt}\nСчёт: {sc}  |  +{earned} монет"
        self.bottom_box.clear_widgets()
        row = BoxLayout(orientation="horizontal", spacing=6)
        if self.net["role"] == "solo":
            btn_r = Button(text="ЕЩЁ РАЗ", background_color=(0.15, 0.39, 0.92, 1), bold=True)
            btn_r.bind(on_press=lambda x: self.start_solo_mode())
            row.add_widget(btn_r)
        btn_m = Button(text="В МЕНЮ", background_color=(0.55, 0.15, 0.15, 1), bold=True)
        btn_m.bind(on_press=self.show_main_menu)
        row.add_widget(btn_m)
        self.bottom_box.add_widget(row)

    def broadcast_state(self):
        if self.net["role"] != "host" or len(self.net["clients"]) == 0:
            return
        payload = (json.dumps({"type": "state", "data": self.state}) + "\n").encode("utf-8")
        for c in self.net["clients"][:]:
            try:
                c["sock"].sendall(payload)
            except Exception:
                self.net["clients"].remove(c)

    def update_loop(self, dt):
        if not hasattr(self, "arena") or self.arena.parent is None:
            return

        if self.net["role"] in ["host", "solo"] and self.match_active and not self.paused and not self.state["game_over"]:
            if self.state["countdown"] > 0:
                self.state["countdown"] = max(0.0, self.state["countdown"] - dt)
                sec = math.ceil(self.state["countdown"])
                self.center_banner.text = f"{sec}..." if sec > 0 else "В БОЙ!"
                if self.state["countdown"] == 0:
                    self.state["started"] = True
                    Clock.schedule_once(lambda d: setattr(self.center_banner, "text", ""), 0.6)
            elif self.state["started"]:
                self.update_physics(dt)
            self.broadcast_state()
        elif self.net["role"] == "client" and self.state.get("countdown", 0) > 0:
            sec = math.ceil(self.state["countdown"])
            self.center_banner.text = f"{sec}..." if sec > 0 else "В БОЙ!"
            if sec == 0:
                Clock.schedule_once(lambda d: setattr(self.center_banner, "text", ""), 0.6)

        my_p = self.state["players"][self.net["my_slot"]]
        self.hp_lbl.text = f"HP: {my_p['hp']}/100"
        self.wave_lbl.text = f"ВОЛНА {self.state['wave']}"
        self.score_lbl.text = f"СЧЁТ: {self.state['score']}"

        self.arena.redraw()


if __name__ == "__main__":
    ShooterApp().run()

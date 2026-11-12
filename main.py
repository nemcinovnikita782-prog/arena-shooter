from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
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

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

class ArenaCanvas(Widget):
    def __init__(self, game_ref, **kwargs):
        super().__init__(**kwargs)
        self.game = game_ref
        self.move_touch_id = None
        self.joy_origin = (0, 0)
        self.joy_curr = (0, 0)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if not self.game.net["running"] or self.game.state["game_over"]:
            return True

        mid_x = self.x + self.width * 0.45
        if touch.x < mid_x and self.move_touch_id is None:
            self.move_touch_id = touch.uid
            self.joy_origin = touch.pos
            self.joy_curr = touch.pos
        else:
            self.game.handle_shoot(touch.x - self.x, touch.y - self.y)
        return True

    def on_touch_move(self, touch):
        if touch.uid == self.move_touch_id:
            self.joy_curr = touch.pos
            dx = touch.x - self.joy_origin[0]
            dy = touch.y - self.joy_origin[1]
            dist = math.hypot(dx, dy)
            if dist > 8:
                self.game.set_move_dir(dx / dist, dy / dist)
            else:
                self.game.set_move_dir(0, 0)
        return True

    def on_touch_up(self, touch):
        if touch.uid == self.move_touch_id:
            self.move_touch_id = None
            self.game.set_move_dir(0, 0)
        return True

    def redraw(self):
        self.canvas.clear()
        st = self.game.state
        ox, oy = self.x, self.y
        w, h = self.width, self.height

        with self.canvas:
            Color(0.13, 0.15, 0.16, 1)
            Rectangle(pos=(ox, oy), size=(w, h))

            Color(0.18, 0.20, 0.22, 1)
            for gx in range(0, int(w), 60):
                Line(points=[ox + gx, oy, ox + gx, oy + h], width=1)
            for gy in range(0, int(h), 60):
                Line(points=[ox, oy + gy, ox + w, oy + gy], width=1)

            for obs in st["obstacles"]:
                Color(0.35, 0.38, 0.42, 1)
                Rectangle(pos=(ox + obs["x"], oy + obs["y"]), size=(obs["w"], obs["h"]))
            for b in st["bullets"]:
                if b["owner"] == 1:
                    Color(0.38, 0.65, 0.98, 1)
                else:
                    Color(0.29, 0.87, 0.50, 1)
                Ellipse(pos=(ox + b["x"] - 6, oy + b["y"] - 6), size=(12, 12))

            for e in st["enemies"]:
                ex, ey = ox + e["x"], oy + e["y"]
                Color(0.94, 0.27, 0.27, 1)
                Ellipse(pos=(ex - 18, ey - 18), size=(36, 36))
                Color(0.3, 0.05, 0.05, 1)
                Rectangle(pos=(ex - 16, ey + 22), size=(32, 5))
                Color(0.13, 0.77, 0.37, 1)
                bar_w = 32 * max(0, e["hp"] / e["max_hp"])
                Rectangle(pos=(ex - 16, ey + 22), size=(bar_w, 5))

            if st["p1"]["hp"] > 0:
                p1x, p1y = ox + st["p1"]["x"], oy + st["p1"]["y"]
                Color(0.23, 0.51, 0.96, 1)
                Ellipse(pos=(p1x - 20, p1y - 20), size=(40, 40))

            if st["p2"]["hp"] > 0:
                p2x, p2y = ox + st["p2"]["x"], oy + st["p2"]["y"]
                Color(0.13, 0.77, 0.37, 1)
                Ellipse(pos=(p2x - 20, p2y - 20), size=(40, 40))

            if self.move_touch_id is not None:
                Color(1, 1, 1, 0.25)
                Ellipse(pos=(self.joy_origin[0] - 45, self.joy_origin[1] - 45), size=(90, 90))
                Color(1, 1, 1, 0.6)
                Ellipse(pos=(self.joy_curr[0] - 20, self.joy_curr[1] - 20), size=(40, 40))

class ShooterApp(App):
    def build(self):
        self.net = {"role": None, "conn": None, "srv": None, "running": False}
        self.spawn_timer = 0.0
        self.reset_state(solo=True)

        self.root_box = BoxLayout(orientation="vertical")
        self.show_main_menu()

        Clock.schedule_interval(self.update_loop, 1.0 / 30.0)
        return self.root_box

    def reset_state(self, solo=False):
        self.state = {
            "p1": {"x": 150.0, "y": 250.0, "vx": 0.0, "vy": 0.0, "hp": 100},
            "p2": {"x": 300.0, "y": 250.0, "vx": 0.0, "vy": 0.0, "hp": 0 if solo else 100},
            "bullets": [],
            "enemies": [],
            "obstacles": [
                {"x": 140, "y": 180, "w": 90, "h": 30},
                {"x": 220, "y": 420, "w": 90, "h": 30}
            ],
            "score": 0,
            "wave": 1,
            "game_over": False
        }

    def close_network(self):
        self.net["running"] = False
        for key in ["conn", "srv"]:
            if self.net[key]:
                try:
                    self.net[key].close()
                except Exception:
                    pass
                self.net[key] = None
        self.net["role"] = None
    def show_main_menu(self, instance=None):
        self.close_network()
        self.root_box.clear_widgets()

        menu = BoxLayout(orientation="vertical", padding=24, spacing=16)

        title = Label(
            text="ARENA SHOOTER",
            font_size="28sp",
            bold=True,
            color=(0, 1, 0.53, 1),
            size_hint=(1, 0.3)
        )
        menu.add_widget(title)

        btn_solo = Button(
            text="ОДИНОЧНАЯ ИГРА (ВОЛНЫ)",
            font_size="16sp",
            bold=True,
            background_color=(0.15, 0.39, 0.92, 1),
            size_hint=(1, 0.16)
        )
        btn_solo.bind(on_press=self.start_solo_mode)
        menu.add_widget(btn_solo)

        btn_lan = Button(
            text="ЛОКАЛЬНАЯ СЕТЬ (С ДРУГОМ)",
            font_size="16sp",
            bold=True,
            background_color=(0.09, 0.64, 0.29, 1),
            size_hint=(1, 0.16)
        )
        btn_lan.bind(on_press=self.open_lan_lobby)
        menu.add_widget(btn_lan)

        btn_online = Button(
            text="ОНЛАЙН-МУЛЬТИПЛЕЕР (СЕРВЕР)",
            font_size="16sp",
            bold=True,
            background_color=(0.85, 0.46, 0.02, 1),
            size_hint=(1, 0.16)
        )
        btn_online.bind(on_press=self.open_online_lobby)
        menu.add_widget(btn_online)

        menu.add_widget(Widget(size_hint=(1, 0.22)))
        self.root_box.add_widget(menu)

    def build_arena_screen(self):
        self.root_box.clear_widgets()

        self.hud = Label(
            text="ПОДГОТОВКА...",
            size_hint=(1, 0.08),
            bold=True,
            color=(0, 1, 0.53, 1)
        )
        self.root_box.add_widget(self.hud)

        self.arena = ArenaCanvas(self, size_hint=(1, 0.78))
        self.root_box.add_widget(self.arena)

        self.bottom_box = BoxLayout(orientation="vertical", size_hint=(1, 0.14), padding=6, spacing=6)
        self.root_box.add_widget(self.bottom_box)

    def start_solo_mode(self, instance):
        self.reset_state(solo=True)
        self.net["role"] = "solo"
        self.net["running"] = True
        self.build_arena_screen()

        row = BoxLayout(orientation="horizontal", spacing=6)
        btn_r = Button(text="ЗАНОВО", background_color=(0.22, 0.25, 0.32, 1), bold=True)
        btn_r.bind(on_press=lambda x: self.reset_state(solo=True))
        btn_m = Button(text="В МЕНЮ", background_color=(0.6, 0.15, 0.15, 1), bold=True)
        btn_m.bind(on_press=self.show_main_menu)

        row.add_widget(btn_r)
        row.add_widget(btn_m)
        self.bottom_box.add_widget(row)

    def open_lan_lobby(self, instance):
        self.reset_state(solo=False)
        self.build_arena_screen()
        self.hud.text = f"Твой IP: {get_local_ip()}"

        self.ip_input = TextInput(
            text="192.168.",
            multiline=False,
            halign="center",
            font_size="18sp"
        )
        self.bottom_box.add_widget(self.ip_input)

        btn_row = BoxLayout(orientation="horizontal", spacing=6)
        btn_host = Button(text="СОЗДАТЬ", background_color=(0.15, 0.39, 0.92, 1), bold=True)
        btn_host.bind(on_press=self.start_host)
        btn_join = Button(text="ВОЙТИ", background_color=(0.09, 0.64, 0.29, 1), bold=True)
        btn_join.bind(on_press=self.start_client)
        btn_back = Button(text="МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True)
        btn_back.bind(on_press=self.show_main_menu)

        btn_row.add_widget(btn_host)
        btn_row.add_widget(btn_join)
        btn_row.add_widget(btn_back)
        self.bottom_box.add_widget(btn_row)

    def open_online_lobby(self, instance):
        self.root_box.clear_widgets()
        box = BoxLayout(orientation="vertical", padding=24, spacing=16)

        box.add_widget(Widget(size_hint=(1, 0.2)))

        status_lbl = Label(
            text="ПОДКЛЮЧЕНИЕ К СЕРВЕРУ...",
            font_size="20sp",
            bold=True,
            color=(0.98, 0.8, 0.08, 1),
            size_hint=(1, 0.15)
        )
        box.add_widget(status_lbl)

        sub_lbl = Label(
            text="Сервер сейчас недоступен или в разработке.\nВход в онлайн-матч заблокирован до ответа сервера.",
            font_size="14sp",
            halign="center",
            color=(0.75, 0.75, 0.75, 1),
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
    def set_move_dir(self, vx, vy):
        if self.net["role"] in ["host", "solo"]:
            self.state["p1"]["vx"] = vx
            self.state["p1"]["vy"] = vy
        elif self.net["role"] == "client" and self.net["conn"]:
            self.send_to_host({"type": "move", "vx": vx, "vy": vy})

    def handle_shoot(self, tx, ty):
        if self.net["role"] in ["host", "solo"] and self.state["p1"]["hp"] > 0:
            self.spawn_bullet(self.state["p1"]["x"], self.state["p1"]["y"], tx, ty, 1)
        elif self.net["role"] == "client" and self.state["p2"]["hp"] > 0:
            self.send_to_host({"type": "shoot", "x": tx, "y": ty})

    def send_to_host(self, payload):
        if self.net["conn"]:
            try:
                self.net["conn"].sendall((json.dumps(payload) + "\n").encode("utf-8"))
            except Exception:
                pass

    def spawn_bullet(self, px, py, tx, ty, owner):
        dx = tx - px
        dy = ty - py
        dist = math.hypot(dx, dy)
        if dist == 0:
            return
        speed = 16.0
        self.state["bullets"].append({
            "x": px,
            "y": py,
            "vx": (dx / dist) * speed,
            "vy": (dy / dist) * speed,
            "owner": owner
        })

    def start_host(self, instance):
        self.net["role"] = "host"
        self.hud.text = f"Ждём друга... Твой IP: {get_local_ip()}"
        self.bottom_box.clear_widgets()
        btn_back = Button(text="ОТМЕНА / В МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True)
        btn_back.bind(on_press=self.show_main_menu)
        self.bottom_box.add_widget(btn_back)
        threading.Thread(target=self.server_worker, daemon=True).start()

    def start_client(self, instance):
        ip = self.ip_input.text.strip()
        self.net["role"] = "client"
        self.hud.text = f"Подключение к {ip}..."
        threading.Thread(target=self.client_worker, args=(ip,), daemon=True).start()

    def server_worker(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.net["srv"] = srv
        try:
            srv.bind(("0.0.0.0", 5555))
            srv.listen(1)
            conn, addr = srv.accept()
        except Exception:
            return

        self.net["conn"] = conn
        self.net["running"] = True
        Clock.schedule_once(lambda dt: self.setup_match_ui(), 0)

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
                    if msg.get("type") == "move":
                        self.state["p2"]["vx"] = msg["vx"]
                        self.state["p2"]["vy"] = msg["vy"]
                    elif msg.get("type") == "shoot" and self.state["p2"]["hp"] > 0:
                        self.spawn_bullet(self.state["p2"]["x"], self.state["p2"]["y"], msg["x"], msg["y"], 2)
            except Exception:
                break

    def client_worker(self, host_ip):
        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            cli.connect((host_ip, 5555))
        except Exception:
            Clock.schedule_once(lambda dt: setattr(self.hud, "text", "Ошибка подключения! Проверь IP"), 0)
            return

        self.net["conn"] = cli
        self.net["running"] = True
        Clock.schedule_once(lambda dt: self.setup_match_ui(), 0)

        buf = ""
        while self.net["running"]:
            try:
                data = cli.recv(8192).decode("utf-8")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    new_st = json.loads(line)
                    self.state.update(new_st)
            except Exception:
                break

    def setup_match_ui(self):
        self.bottom_box.clear_widgets()
        row = BoxLayout(orientation="horizontal", spacing=6)
        if self.net["role"] == "host":
            btn_r = Button(text="ЗАНОВО (ХОСТ)", background_color=(0.22, 0.25, 0.32, 1), bold=True)
            btn_r.bind(on_press=lambda x: self.reset_state(solo=False))
            row.add_widget(btn_r)
        else:
            lbl = Label(text="Зелёный боец")
            row.add_widget(lbl)

        btn_m = Button(text="В МЕНЮ", background_color=(0.5, 0.15, 0.15, 1), bold=True)
        btn_m.bind(on_press=self.show_main_menu)
        row.add_widget(btn_m)
        self.bottom_box.add_widget(row)

    def point_in_obstacle(self, x, y, r=0):
        for obs in self.state["obstacles"]:
            if (obs["x"] - r <= x <= obs["x"] + obs["w"] + r) and (obs["y"] - r <= y <= obs["y"] + obs["h"] + r):
                return True
        return False

    def update_physics(self, dt):
        w = max(300.0, self.arena.width)
        h = max(400.0, self.arena.height)

        for key in ["p1", "p2"]:
            p = self.state[key]
            if p["hp"] <= 0:
                continue
            nx = max(20.0, min(w - 20.0, p["x"] + p["vx"] * 5.0))
            ny = max(20.0, min(h - 20.0, p["y"] + p["vy"] * 5.0))
            if not self.point_in_obstacle(nx, ny, 16):
                p["x"], p["y"] = nx, ny

        for b in self.state["bullets"][:]:
            b["x"] += b["vx"]
            b["y"] += b["vy"]
            if b["x"] < 0 or b["x"] > w or b["y"] < 0 or b["y"] > h or self.point_in_obstacle(b["x"], b["y"], 4):
                self.state["bullets"].remove(b)

        self.spawn_timer += dt
        spawn_delay = max(0.7, 2.2 - self.state["wave"] * 0.15)
        if self.spawn_timer >= spawn_delay:
            self.spawn_timer = 0.0
            side = random.choice(["top", "bottom", "left", "right"])
            if side == "top":
                ex, ey = random.uniform(20, w - 20), h - 10
            elif side == "bottom":
                ex, ey = random.uniform(20, w - 20), 10
            elif side == "left":
                ex, ey = 10, random.uniform(20, h - 20)
            else:
                ex, ey = w - 10, random.uniform(20, h - 20)
            hp = 2 + (self.state["wave"] // 2)
            spd = min(4.0, 1.5 + self.state["wave"] * 0.2)
            self.state["enemies"].append({"x": ex, "y": ey, "hp": hp, "max_hp": hp, "speed": spd})

        for e in self.state["enemies"][:]:
            targets = [self.state[k] for k in ["p1", "p2"] if self.state[k]["hp"] > 0]
            if not targets:
                self.state["game_over"] = True
                break
            target = min(targets, key=lambda t: math.hypot(t["x"] - e["x"], t["y"] - e["y"]))
            edx = target["x"] - e["x"]
            edy = target["y"] - e["y"]
            edist = math.hypot(edx, edy)
            if edist > 0:
                e["x"] += (edx / edist) * e["speed"]
                e["y"] += (edy / edist) * e["speed"]

            if edist < 26:
                target["hp"] = max(0, target["hp"] - 15)
                self.state["enemies"].remove(e)
                if self.state["p1"]["hp"] <= 0 and self.state["p2"]["hp"] <= 0:
                    self.state["game_over"] = True
                continue

            for b in self.state["bullets"][:]:
                if math.hypot(b["x"] - e["x"], b["y"] - e["y"]) < 22:
                    e["hp"] -= 1
                    if b in self.state["bullets"]:
                        self.state["bullets"].remove(b)
                    if e["hp"] <= 0:
                        if e in self.state["enemies"]:
                            self.state["enemies"].remove(e)
                        self.state["score"] += 10
                        self.state["wave"] = 1 + (self.state["score"] // 100)
                    break

        if self.net["role"] == "host" and self.net["conn"]:
            try:
                self.net["conn"].sendall((json.dumps(self.state) + "\n").encode("utf-8"))
            except Exception:
                self.net["running"] = False

    def update_loop(self, dt):
        if not hasattr(self, "arena") or self.arena.parent is None:
            return

        if not self.net["running"]:
            self.arena.redraw()
            return

        if self.net["role"] in ["host", "solo"] and not self.state["game_over"]:
            self.update_physics(dt)

        st = self.state
        if st["game_over"]:
            self.hud.text = f"ИГРА ОКОНЧЕНА! Счёт: {st['score']} | Волна: {st['wave']}"
        elif self.net["role"] == "solo":
            self.hud.text = f"HP: {st['p1']['hp']} | СЧЁТ: {st['score']} | ВОЛНА: {st['wave']}"
        else:
            self.hud.text = f"СИНИЙ: {st['p1']['hp']} HP | ЗЕЛЁНЫЙ: {st['p2']['hp']} HP | СЧЁТ: {st['score']} | В: {st['wave']}"

        self.arena.redraw()

if __name__ == "__main__":
    ShooterApp().run()

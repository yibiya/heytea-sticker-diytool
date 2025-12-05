import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import numpy as np
import pyautogui
import threading
import time
import keyboard
from PIL import Image, ImageTk
import subprocess
import os

# --- 全局主题设置 ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

# 定义配色
THEME_COLOR = "#FF69B4"
ACCENT_COLOR = "#00E5FF"
BG_COLOR = "#1a1a1a"
CARD_COLOR = "#2b2b2b"

class ScreenAreaSelector:
    def __init__(self, master, callback):
        self.master = master
        self.callback = callback
        self.top = tk.Toplevel(master)
        self.top.attributes('-fullscreen', True)
        self.top.attributes('-alpha', 0.3)
        self.top.attributes('-topmost', True)
        self.top.configure(background='black')
        self.top.config(cursor="crosshair")
        
        self.start_x = None
        self.start_y = None
        self.rect_id = None
        
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        
        cx, cy = self.top.winfo_screenwidth()//2, self.top.winfo_screenheight()//2
        self.canvas.create_text(cx, cy, 
            text="请按住左键框选绘图区域\n(如使用投屏，请直接框选投屏窗口)\nESC退出", 
            fill="white", font=("Microsoft YaHei UI", 20, "bold"), justify=tk.CENTER)
        
        keyboard.add_hotkey('esc', self.close_selector)

    def close_selector(self):
        try: self.top.destroy()
        except: pass

    def on_mouse_down(self, event):
        self.start_x = event.x
        self.start_y = event.y

    def on_mouse_drag(self, event):
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y, 
            outline=ACCENT_COLOR, width=2)

    def on_mouse_up(self, event):
        x1, y1, x2, y2 = self.start_x, self.start_y, event.x, event.y
        left, right = sorted([x1, x2])
        top, bottom = sorted([y1, y2])
        width, height = right - left, bottom - top
        self.top.destroy()
        if width < 10 or height < 10: return
        self.callback((left, top, width, height))

class ModernAutoDrawApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("喜茶喜贴 diy 工具 (Pro UI)")
        self.geometry("1100x750")
        
        self.src_img = None       
        self.final_paths = []     
        self.target_area = None
        self.is_running = False
        self.drawing_thread = None
        self.debounce_timer = None
        self.scrcpy_process = None
        
        self.use_scrcpy = ctk.BooleanVar(value=False)
        self.var_canny = ctk.IntVar(value=10)      # 原始默认值 100
        self.var_min_len = ctk.IntVar(value=10)     # 原始默认值 20
        self.var_connect = ctk.IntVar(value=5)      # 原始默认值 5
        self.var_delay = ctk.DoubleVar(value=0.05)  # 原始默认值 0.05
        
        self._init_layout()
        self._init_hotkeys()

    def _init_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=300, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(10, weight=1)

        # Logo
        ctk.CTkLabel(self.sidebar, text="AUTO\nPAINTER", 
                     font=("Microsoft YaHei UI", 32, "bold"), text_color=THEME_COLOR).grid(row=0, column=0, padx=20, pady=(30, 10), sticky="w")
        
        # Buttons
        self.btn_load = ctk.CTkButton(self.sidebar, text="1. 加载素材", fg_color=ACCENT_COLOR, text_color="black", 
                                    font=("Microsoft YaHei UI", 14, "bold"), height=40, command=self.load_image)
        self.btn_load.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        self.btn_area = ctk.CTkButton(self.sidebar, text="2. 锁定区域 (F8)", fg_color="transparent", border_width=2, border_color=THEME_COLOR, 
                                    text_color=THEME_COLOR, font=("Microsoft YaHei UI", 14, "bold"), height=40, command=self.select_screen_area)
        self.btn_area.grid(row=3, column=0, padx=20, pady=10, sticky="ew")

        ctk.CTkSwitch(self.sidebar, text="启动 Scrcpy 投屏", variable=self.use_scrcpy, 
                      progress_color=THEME_COLOR, command=self.toggle_scrcpy_mode).grid(row=4, column=0, padx=20, pady=20, sticky="w")

        # Settings
        self.settings_frame = ctk.CTkFrame(self.sidebar, fg_color=CARD_COLOR, corner_radius=15)
        self.settings_frame.grid(row=5, column=0, padx=15, pady=10, sticky="ew")
        
        # Canny: 10-255
        self._create_slider(self.settings_frame, "边缘细节", self.var_canny, 255, 10, 0)
        # Min Len: 0-100
        self._create_slider(self.settings_frame, "忽略短线", self.var_min_len, 0, 100, 2)
        # Connect: 0-50
        self._create_slider(self.settings_frame, "自动连接", self.var_connect, 0, 50, 4)
        # Delay: 0.0-0.5
        self._create_slider(self.settings_frame, "笔画间隔(s)", self.var_delay, 0.0, 0.5, 6, is_float=True)

        # Start/Stop
        self.btn_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.btn_frame.grid(row=11, column=0, padx=20, pady=20, sticky="ew")
        self.btn_start = ctk.CTkButton(self.btn_frame, text="开始 (F9)", fg_color="#00C853", width=120, height=50, command=self.on_f9_press)
        self.btn_start.pack(side="left", padx=5)
        self.btn_stop = ctk.CTkButton(self.btn_frame, text="停止 (F10)", fg_color="#D50000", width=100, height=50, command=self.on_f10_press)
        self.btn_stop.pack(side="right", padx=5)

        # Main Area
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_columnconfigure(0, weight=1)

        self.canvas_container = ctk.CTkFrame(self.right_frame, fg_color="#000000", corner_radius=15, border_width=2, border_color="#333")
        self.canvas_container.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        
        self.canvas = tk.Canvas(self.canvas_container, bg="#111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.status_bar = ctk.CTkLabel(self.right_frame, text="系统就绪 | 等待操作...", fg_color=CARD_COLOR, corner_radius=8, height=35, anchor="w", padx=15)
        self.status_bar.grid(row=1, column=0, sticky="ew")

    def _create_slider(self, parent, title, variable, min_v, max_v, row, is_float=False):
        ctk.CTkLabel(parent, text=title, font=("Microsoft YaHei UI", 12, "bold")).grid(row=row, column=0, padx=10, pady=(10,0), sticky="w")
        val_lbl = ctk.CTkLabel(parent, text=str(variable.get()), text_color=ACCENT_COLOR)
        val_lbl.grid(row=row, column=1, padx=10, pady=(10,0), sticky="e")
        
        def update(val):
            v = float(val) if is_float else int(val)
            val_lbl.configure(text=f"{v:.2f}" if is_float else str(v))
            self.trigger_update()

        ctk.CTkSlider(parent, from_=min_v, to=max_v, variable=variable, command=update, progress_color=THEME_COLOR).grid(row=row+1, column=0, columnspan=2, padx=10, pady=(0,10), sticky="ew")

    def _init_hotkeys(self):
        try:
            keyboard.add_hotkey('f8', self.select_screen_area)
            keyboard.add_hotkey('f9', self.on_f9_press)
            keyboard.add_hotkey('f10', self.on_f10_press)
        except: pass

    def update_status(self, text):
        self.status_bar.configure(text=f">> {text}")

    def toggle_scrcpy_mode(self):
        if self.use_scrcpy.get(): self.launch_scrcpy()
        else: self.update_status("已切换至桌面模式")

    def launch_scrcpy(self):
        cwd = os.getcwd()
        scrcpy_path = os.path.join(cwd, "scrcpy", "scrcpy.exe")
        if not os.path.exists(scrcpy_path):
            self.update_status("错误: 未找到 scrcpy/scrcpy.exe")
            return
        try:
            self.scrcpy_process = subprocess.Popen([scrcpy_path, "--stay-awake", "--window-title=喜茶创作投屏"], cwd=os.path.dirname(scrcpy_path))
            self.update_status("投屏服务已启动")
        except Exception as e:
            self.update_status(f"启动失败: {e}")

    def load_image(self):
        path = filedialog.askopenfilename(filetypes=[("Image Files", "*.jpg *.png *.bmp *.jpeg")])
        if not path: return
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), -1)
        if img is None: return
        if len(img.shape) == 3: img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        
        h, w = img.shape[:2]
        max_dim = 800
        if w > max_dim or h > max_dim:
            scale = max_dim / max(w, h)
            img = cv2.resize(img, (int(w*scale), int(h*scale)))
            
        self.src_img = img 
        self.run_processing_task()

    def trigger_update(self, val=None):
        if self.src_img is None: return
        if self.debounce_timer: self.after_cancel(self.debounce_timer)
        self.update_status("参数计算中...")
        self.debounce_timer = self.after(300, self.run_processing_task)

    def run_processing_task(self):
        self.update_idletasks()
        self.process_image_logic()
        self.debounce_timer = None

    def process_image_logic(self):
        if self.src_img is None: return
        gray = cv2.cvtColor(self.src_img, cv2.COLOR_BGR2GRAY)
        canny_thresh = self.var_canny.get()
        edges = cv2.Canny(gray, canny_thresh // 2, canny_thresh)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        
        raw_paths = []
        for cnt in contours:
            raw_paths.append(cnt.reshape(-1, 2).tolist())

        self.final_paths = self.optimize_paths(raw_paths)
        
        h, w = edges.shape
        preview = np.zeros((h, w), dtype=np.uint8)
        for path in self.final_paths:
            if len(path) > 1:
                pts = np.array(path, np.int32).reshape((-1, 1, 2))
                cv2.polylines(preview, [pts], False, (255), 1)
        
        self.update_canvas_preview(preview)
        self.update_status(f"处理完毕 | 路径数: {len(self.final_paths)}")

    def optimize_paths(self, raw_paths):
        min_len = self.var_min_len.get()
        connect_dist_sq = self.var_connect.get() ** 2
        valid_paths = [p for p in raw_paths if len(p) > min_len]
        if not valid_paths: return []

        merged_paths = []
        while valid_paths:
            current_path = valid_paths.pop(0)
            while True:
                end_pt = current_path[-1]
                best_idx = -1
                min_d = float('inf')
                should_reverse = False
                search_limit = min(len(valid_paths), 200)
                
                for i in range(search_limit):
                    p = valid_paths[i]
                    d1 = (p[0][0] - end_pt[0])**2 + (p[0][1] - end_pt[1])**2
                    d2 = (p[-1][0] - end_pt[0])**2 + (p[-1][1] - end_pt[1])**2
                    curr_min = min(d1, d2)
                    if curr_min < min_d:
                        min_d = curr_min
                        best_idx = i
                        should_reverse = (d2 < d1)
                
                if best_idx != -1 and min_d <= connect_dist_sq:
                    next_path = valid_paths.pop(best_idx)
                    if should_reverse: next_path.reverse()
                    current_path.extend(next_path)
                else:
                    break
            merged_paths.append(current_path)
        return merged_paths

    def update_canvas_preview(self, img_data):
        self.canvas.update()
        h, w = img_data.shape
        c_w, c_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if c_w < 10: c_w, c_h = 800, 500
        
        ratio = min(c_w/w, c_h/h) * 0.95
        new_w, new_h = int(w*ratio), int(h*ratio)
        
        resized = cv2.resize(img_data, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        
        self.tk_img = ImageTk.PhotoImage(Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(c_w//2, c_h//2, image=self.tk_img)

    def select_screen_area(self):
        self.withdraw()
        self.after(200, lambda: ScreenAreaSelector(self, self.on_area_selected))

    def on_area_selected(self, area):
        self.deiconify()
        self.target_area = area
        self.update_status(f"区域锁定: {area} | 等待开始 [F9]")

    def on_f9_press(self):
        if not self.target_area or not self.final_paths: 
            messagebox.showwarning("提示", "请先加载素材并框选区域")
            return
        if not self.is_running: self.start_drawing()

    def on_f10_press(self):
        if self.is_running: self.stop_drawing()

    def start_drawing(self):
        self.is_running = True
        self.drawing_thread = threading.Thread(target=self._drawing_process)
        self.drawing_thread.daemon = True
        self.drawing_thread.start()

    def stop_drawing(self):
        self.is_running = False
        self.update_status("正在停止...")

    def _drawing_process(self):
        stroke_delay = self.var_delay.get() # 使用 var.get()
        if self.use_scrcpy.get():
            stroke_delay = max(stroke_delay, 0.05)

        img_h, img_w = self.src_img.shape[:2]
        tx, ty, tw, th = self.target_area
        
        scale = min(tw / img_w, th / img_h)
        offset_x = tx + (tw - img_w * scale) / 2
        offset_y = ty + (th - img_h * scale) / 2
        
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.005 

        self.update_status(">>> 绘制进行中... [F10] 暂停/停止 <<<")
        total_paths = len(self.final_paths)

        try:
            for i, path in enumerate(self.final_paths):
                if not self.is_running: break
                
                screen_points = []
                for px, py in path:
                    sx = int(offset_x + px * scale)
                    sy = int(offset_y + py * scale)
                    screen_points.append((sx, sy))
                
                if len(screen_points) < 2: continue

                start_x, start_y = screen_points[0]
                
                pyautogui.keyUp('shift') 
                pyautogui.mouseUp()
                
                if self.use_scrcpy.get():
                    time.sleep(0.05) 

                pyautogui.moveTo(start_x, start_y)
                time.sleep(0.02) 

                pyautogui.mouseDown()
                time.sleep(0.05)

                step = 2
                for j in range(1, len(screen_points), step):
                    if not self.is_running: break
                    x, y = screen_points[j]
                    pyautogui.moveTo(x, y)
                
                end_x, end_y = screen_points[-1]
                pyautogui.moveTo(end_x, end_y)
                
                time.sleep(0.02) 
                pyautogui.mouseUp()
                
                if self.use_scrcpy.get():
                    time.sleep(0.05)

                if i % 10 == 0 or i == total_paths - 1: 
                    self.after(0, lambda p=i: self.update_status(f"进度: {p + 1}/{total_paths}"))
                
                if stroke_delay > 0:
                    time.sleep(stroke_delay)
                
        except Exception as e:
            self.after(0, lambda e=e: self.update_status(f"异常中断: {e}"))
        finally:
            pyautogui.mouseUp()
            self.is_running = False
            self.after(0, lambda: self.update_status("绘制任务结束"))

if __name__ == "__main__":
    app = ModernAutoDrawApp()
    app.mainloop()
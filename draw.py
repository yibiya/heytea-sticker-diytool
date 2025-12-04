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
import math

class ScreenAreaSelector:
    def __init__(self, master, callback):
        self.master = master
        self.callback = callback
        self.top = tk.Toplevel(master)
        self.top.attributes('-fullscreen', True)
        self.top.attributes('-alpha', 0.3)
        self.top.configure(background='black')
        self.top.config(cursor="cross")
        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.create_text(self.top.winfo_screenwidth()//2, self.top.winfo_screenheight()//2, 
            text="请按住左键框选绘图区域\n松开完成设定", fill="white", font=("Arial", 20))

    def on_mouse_down(self, event):
        self.start_x = event.x
        self.start_y = event.y

    def on_mouse_drag(self, event):
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="#00ff00", width=2)

    def on_mouse_up(self, event):
        x1, y1, x2, y2 = self.start_x, self.start_y, event.x, event.y
        left, right = sorted([x1, x2])
        top, bottom = sorted([y1, y2])
        width, height = right - left, bottom - top
        self.top.destroy()
        if width < 10 or height < 10: return
        self.callback((left, top, width, height))

class ADBImageSelector:
    def __init__(self, master, cv_image, callback):
        self.master = master
        self.callback = callback
        self.original_image = cv_image
        self.h, self.w = cv_image.shape[:2]
        
        self.top = tk.Toplevel(master)
        self.top.title("请在手机截图中框选绘图区域")
        self.top.state('zoomed') 
        
        screen_h = self.top.winfo_screenheight() - 100
        self.display_scale = 1.0
        if self.h > screen_h:
            self.display_scale = screen_h / self.h
            
        display_w = int(self.w * self.display_scale)
        display_h = int(self.h * self.display_scale)
        
        resized = cv2.resize(cv_image, (display_w, display_h))
        resized = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        self.tk_img = ImageTk.PhotoImage(Image.fromarray(resized))
        
        self.canvas = tk.Canvas(self.top, width=display_w, height=display_h, cursor="cross")
        self.canvas.pack(padx=10, pady=10)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)
        
        self.start_x = None
        self.start_y = None
        self.rect_id = None
        
        self.canvas.bind("<Button-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

    def on_mouse_down(self, event):
        self.start_x = event.x
        self.start_y = event.y

    def on_mouse_drag(self, event):
        if self.rect_id: self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline="#FF0000", width=2)

    def on_mouse_up(self, event):
        x1, y1, x2, y2 = self.start_x, self.start_y, event.x, event.y
        left, right = sorted([x1, x2])
        top, bottom = sorted([y1, y2])
        
        real_left = int(left / self.display_scale)
        real_top = int(top / self.display_scale)
        real_w = int((right - left) / self.display_scale)
        real_h = int((bottom - top) / self.display_scale)
        
        self.top.destroy()
        if real_w < 10 or real_h < 10: return
        self.callback((real_left, real_top, real_w, real_h))

class AutoDrawApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VectorPath Pro - Automation Assistant")
        self.root.geometry("1000x900")
        
        self.src_img = None        
        self.processed_preview = None 
        self.final_paths = []      
        self.target_area = None
        self.is_running = False
        self.drawing_thread = None
        self.debounce_timer = None
        
        self.use_adb = tk.BooleanVar(value=False)
        self.adb_process = None 
        
        ctrl_frame = tk.Frame(root, pady=10, bg="#f0f0f0")
        ctrl_frame.pack(side=tk.TOP, fill=tk.X)
        
        row1 = tk.Frame(ctrl_frame, bg="#f0f0f0")
        row1.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        tk.Button(row1, text="1. 上传图片", command=self.load_image, bg="white").pack(side=tk.LEFT, padx=5)
        tk.Label(row1, text="|", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        tk.Checkbutton(row1, text="ADB 模式", variable=self.use_adb, command=self.toggle_adb_mode, bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        self.btn_adb_cap = tk.Button(row1, text="获取手机截屏", command=self.capture_adb_screen, bg="#ffcc80", state=tk.DISABLED)
        self.btn_adb_cap.pack(side=tk.LEFT, padx=5)
        
        tk.Label(row1, text="|", bg="#f0f0f0").pack(side=tk.LEFT, padx=5)
        
        self.btn_select_area = tk.Button(row1, text="2. 框选区域", command=self.select_screen_area, bg="#b3e5fc")
        self.btn_select_area.pack(side=tk.LEFT, padx=5)

        param_frame = tk.LabelFrame(ctrl_frame, text="图片处理参数", bg="#f0f0f0", padx=5, pady=5)
        param_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        tk.Label(param_frame, text="边缘细节:", bg="#f0f0f0", font=("Arial", 9)).grid(row=0, column=0)
        self.scale_canny = tk.Scale(param_frame, from_=255, to=10, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=0, length=120)
        self.scale_canny.set(100)
        self.scale_canny.grid(row=0, column=1)

        tk.Label(param_frame, text="忽略短线:", bg="#f0f0f0", font=("Arial", 9)).grid(row=0, column=2)
        self.scale_min_len = tk.Scale(param_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=1, length=120)
        self.scale_min_len.set(10) 
        self.scale_min_len.grid(row=0, column=3)

        tk.Label(param_frame, text="自动连接:", bg="#f0f0f0", font=("Arial", 9)).grid(row=0, column=4)
        self.scale_connect = tk.Scale(param_frame, from_=0, to=50, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=1, length=120)
        self.scale_connect.set(5) 
        self.scale_connect.grid(row=0, column=5)
        
        draw_frame = tk.LabelFrame(ctrl_frame, text="通用绘制参数", bg="#f0f0f0", padx=5, pady=5)
        draw_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        tk.Label(draw_frame, text="笔画间隔(秒):", bg="#f0f0f0", font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        self.scale_delay = tk.Scale(draw_frame, from_=0.0, to=0.5, resolution=0.01, orient=tk.HORIZONTAL, length=120)
        self.scale_delay.set(0.01)
        self.scale_delay.pack(side=tk.LEFT, padx=5)

        adb_frame = tk.LabelFrame(ctrl_frame, text="ADB 高级限制参数 (仅ADB模式生效)", bg="#e1f5fe", padx=5, pady=5)
        adb_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        tk.Label(adb_frame, text="1. 拟合细节:", bg="#e1f5fe", font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        self.scale_adb_epsilon = tk.Scale(adb_frame, from_=0.0001, to=0.005, resolution=0.0001, orient=tk.HORIZONTAL, length=120, bg="#e1f5fe")
        self.scale_adb_epsilon.set(0.001)
        self.scale_adb_epsilon.pack(side=tk.LEFT, padx=5)

        tk.Label(adb_frame, text="2. 防误触距离(px):", bg="#e1f5fe", font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        self.scale_adb_dist = tk.Scale(adb_frame, from_=10, to=50, orient=tk.HORIZONTAL, length=120, bg="#e1f5fe")
        self.scale_adb_dist.set(20)
        self.scale_adb_dist.pack(side=tk.LEFT, padx=5)

        tk.Label(adb_frame, text="3. 最小耗时(ms):", bg="#e1f5fe", font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        self.scale_adb_duration = tk.Scale(adb_frame, from_=50, to=300, orient=tk.HORIZONTAL, length=120, bg="#e1f5fe")
        self.scale_adb_duration.set(160)
        self.scale_adb_duration.pack(side=tk.LEFT, padx=5)

        self.info_frame = tk.LabelFrame(root, text="状态", padx=10, pady=5)
        self.info_frame.pack(fill=tk.X, padx=10)
        self.lbl_status = tk.Label(self.info_frame, text="准备就绪。快捷键: [F9] 开始 | [F10] 停止", fg="#333", font=("Arial", 10))
        self.lbl_status.pack(side=tk.LEFT)

        self.canvas = tk.Canvas(root, bg="#222")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        try:
            keyboard.add_hotkey('f9', self.on_f9_press)
            keyboard.add_hotkey('f10', self.on_f10_press)
        except:
            pass

    def toggle_adb_mode(self):
        if self.use_adb.get():
            self.btn_adb_cap.config(state=tk.NORMAL)
            self.btn_select_area.config(text="2. 框选区域 (需先获取截屏)", state=tk.DISABLED)
            self.lbl_status.config(text="ADB 模式已开启。请确保手机已连接并开启调试。")
        else:
            self.btn_adb_cap.config(state=tk.DISABLED)
            self.btn_select_area.config(text="2. 框选区域", state=tk.NORMAL)
            self.lbl_status.config(text="切换回 PC 鼠标模式。")

    def get_adb_screenshot_data(self):
        try:
            process = subprocess.Popen("adb shell screencap -p", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            data, err = process.communicate()
            if err: return None
            return data.replace(b'\r\n', b'\n')
        except:
            return None

    def capture_adb_screen(self):
        self.lbl_status.config(text="正在获取手机截屏...")
        self.root.update()
        
        data = self.get_adb_screenshot_data()
        if not data:
            messagebox.showerror("错误", "无法获取截屏，请检查 ADB 连接。")
            self.lbl_status.config(text="截屏获取失败")
            return

        try:
            arr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None: raise Exception("Decode failed")
            
            self.lbl_status.config(text="截屏成功，请在新窗口中框选。")
            ADBImageSelector(self.root, img, self.on_area_selected)
        except Exception as e:
            messagebox.showerror("错误", f"图像解析失败: {e}")

    def load_image(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.jpg *.png *.bmp *.jpeg")])
        if not path: return
        img = cv2.imread(path)
        if img is None: return
        
        h, w = img.shape[:2]
        max_dim = 800
        if w > max_dim or h > max_dim:
            scale = max_dim / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = cv2.resize(img, (new_w, new_h))
            
        self.src_img = img 
        self.run_processing_task()

    def trigger_update(self, val=None):
        if self.src_img is None: return
        if self.debounce_timer:
            self.root.after_cancel(self.debounce_timer)
        self.lbl_status.config(text="等待调整...")
        self.debounce_timer = self.root.after(300, self.run_processing_task)

    def run_processing_task(self):
        self.lbl_status.config(text="正在计算...")
        self.root.update_idletasks()
        self.process_image_logic()
        self.debounce_timer = None

    def process_image_logic(self):
        if self.src_img is None: return
        
        gray = cv2.cvtColor(self.src_img, cv2.COLOR_BGR2GRAY)
        canny_thresh = self.scale_canny.get()
        edges = cv2.Canny(gray, canny_thresh // 2, canny_thresh)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        
        raw_paths = []
        for cnt in contours:
            pts = cnt.reshape(-1, 2).tolist()
            raw_paths.append(pts)

        self.final_paths = self.optimize_paths(raw_paths)
        
        h, w = edges.shape
        preview = np.zeros((h, w), dtype=np.uint8)
        
        for path in self.final_paths:
            if len(path) > 1:
                pts = np.array(path, np.int32).reshape((-1, 1, 2))
                cv2.polylines(preview, [pts], False, (255), 1)
        
        self.update_canvas_preview(preview)
        self.lbl_status.config(text=f"计算完成: 提取出 {len(self.final_paths)} 条笔画")

    def optimize_paths(self, raw_paths):
        min_len = self.scale_min_len.get()
        connect_dist_sq = self.scale_connect.get() ** 2
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
                else: break
            merged_paths.append(current_path)
        return merged_paths

    def update_canvas_preview(self, img_data):
        h, w = img_data.shape
        c_w, c_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if c_w < 10: c_w, c_h = 800, 500
        
        ratio = min(c_w/w, c_h/h) * 0.95
        new_w, new_h = int(w*ratio), int(h*ratio)
        
        resized = cv2.resize(img_data, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        resized = cv2.bitwise_not(resized) 
        
        self.tk_img = ImageTk.PhotoImage(Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(c_w//2, c_h//2, image=self.tk_img)

    def select_screen_area(self):
        if self.use_adb.get():
            self.capture_adb_screen()
        else:
            self.root.withdraw()
            self.root.after(200, lambda: ScreenAreaSelector(self.root, self.on_area_selected))

    def on_area_selected(self, area):
        if not self.use_adb.get():
            self.root.deiconify()
        self.target_area = area 
        mode_str = "ADB" if self.use_adb.get() else "屏幕"
        self.lbl_status.config(text=f"{mode_str}区域已锁定。按 [F9] 开始绘制。")

    def on_f9_press(self):
        if not self.target_area or not self.final_paths: 
            messagebox.showwarning("提示", "请先加载图片并框选区域")
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
        self.lbl_status.config(text="正在停止...")

    def adb_swipe_pipe(self, x1, y1, x2, y2, duration=100):
        if self.adb_process is None or self.adb_process.poll() is not None:
            self.adb_process = subprocess.Popen(
                ["adb", "shell"], 
                stdin=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                text=True, 
                bufsize=0 
            )
        
        try:
            cmd = f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration)}\n"
            self.adb_process.stdin.write(cmd)
            self.adb_process.stdin.flush()
            wait_time = (duration / 1000.0) + 0.02
            time.sleep(wait_time)
        except Exception:
            pass

    def filter_points_adaptive(self, points, min_dist=20):
        if not points or len(points) < 2: return points
        
        filtered = [points[0]]
        for pt in points[1:]:
            last = filtered[-1]
            dist = math.sqrt((pt[0]-last[0])**2 + (pt[1]-last[1])**2)
            if dist >= min_dist:
                filtered.append(pt)
        
        if filtered[-1] != points[-1]:
             last_dist = math.sqrt((filtered[-1][0]-points[-1][0])**2 + (filtered[-1][1]-points[-1][1])**2)
             if last_dist > 5:
                 filtered.append(points[-1])
                 
        return filtered

    def _drawing_process(self):
        stroke_delay = self.scale_delay.get()
        img_h, img_w = self.src_img.shape[:2]
        tx, ty, tw, th = self.target_area
        
        scale = min(tw / img_w, th / img_h)
        offset_x = tx + (tw - img_w * scale) / 2
        offset_y = ty + (th - img_h * scale) / 2
        
        if not self.use_adb.get():
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.005 

        if self.use_adb.get():
            try:
                self.adb_process = subprocess.Popen(
                    ["adb", "shell"], 
                    stdin=subprocess.PIPE, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL,
                    text=True, 
                    bufsize=0  
                )
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"ADB 启动失败: {e}"))
                self.is_running = False
                return

        self.root.after(0, lambda: self.lbl_status.config(text=">>> 正在绘制中... 按 [F10] 停止 <<<"))
        
        adb_epsilon_factor = self.scale_adb_epsilon.get()
        adb_min_dist = self.scale_adb_dist.get()          
        adb_min_duration = self.scale_adb_duration.get()  
        
        try:
            total_paths = len(self.final_paths)
            for i, path in enumerate(self.final_paths):
                if not self.is_running: break
                
                target_points = []
                for px, py in path:
                    sx = offset_x + px * scale
                    sy = offset_y + py * scale
                    target_points.append([sx, sy])
                
                if len(target_points) < 2: continue

                if self.use_adb.get():
                    pts_np = np.array(target_points, dtype=np.int32)
                    arc_len = cv2.arcLength(pts_np, False)
                    epsilon = adb_epsilon_factor * arc_len 
                    approx_curve = cv2.approxPolyDP(pts_np, epsilon, False)
                    curve_points = approx_curve.reshape(-1, 2).tolist()
                    
                    final_points = self.filter_points_adaptive(curve_points, min_dist=adb_min_dist)
                    
                    if len(final_points) < 2:
                        p_start = target_points[0]
                        p_end = target_points[-1]
                        
                        dx = p_end[0] - p_start[0]
                        dy = p_end[1] - p_start[1]
                        real_len = math.sqrt(dx*dx + dy*dy)
                        
                        if real_len > 0:
                            factor = float(adb_min_dist) / real_len
                            new_x = p_start[0] + dx * factor
                            new_y = p_start[1] + dy * factor
                            final_points = [p_start, [new_x, new_y]]
                        else:
                            final_points = []

                    if len(final_points) > 1:
                        for j in range(len(final_points) - 1):
                            if not self.is_running: break
                            p1 = final_points[j]
                            p2 = final_points[j+1]
                            
                            dist = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                            
                            duration = int(max(adb_min_duration, min(1500, adb_min_duration + dist * 1.1)))
                            
                            self.adb_swipe_pipe(p1[0], p1[1], p2[0], p2[1], duration)
                    
                    if stroke_delay > 0: time.sleep(stroke_delay)

                else:
                    start_x, start_y = int(target_points[0][0]), int(target_points[0][1])
                    pyautogui.keyUp('shift')
                    pyautogui.mouseUp()
                    pyautogui.moveTo(start_x, start_y)
                    time.sleep(0.02)
                    pyautogui.mouseDown()
                    time.sleep(0.05)
                    step = 2 
                    for j in range(1, len(target_points), step):
                        if not self.is_running: break
                        x, y = int(target_points[j][0]), int(target_points[j][1])
                        pyautogui.moveTo(x, y)
                    end_x, end_y = int(target_points[-1][0]), int(target_points[-1][1])
                    pyautogui.moveTo(end_x, end_y)
                    time.sleep(0.02)
                    pyautogui.mouseUp()
                    if stroke_delay > 0: time.sleep(stroke_delay)
                
                interval = 1 if self.use_adb.get() else 5
                if i % interval == 0 or i == total_paths - 1:
                    status_text = f"绘制中... 进度: {i + 1}/{total_paths}"
                    self.root.after(0, lambda t=status_text: self.lbl_status.config(text=t))
                    self.root.after(0, self.root.update_idletasks)
                
        except Exception as e:
            self.root.after(0, lambda: self.lbl_status.config(text=f"错误: {e}"))
        finally:
            if self.use_adb.get() and self.adb_process:
                try:
                    self.adb_process.stdin.close()
                    self.adb_process.terminate()
                    self.adb_process.wait(timeout=1)
                except:
                    pass
                self.adb_process = None

            if not self.use_adb.get():
                pyautogui.mouseUp()
            self.is_running = False
            self.root.after(0, lambda: self.lbl_status.config(text="绘制结束。"))

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoDrawApp(root)
    root.mainloop()

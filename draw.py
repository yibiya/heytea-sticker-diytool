import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import numpy as np
import pyautogui
import threading
import time
import keyboard
from PIL import Image, ImageTk

# 不需要递归限制了，因为我们改用了 OpenCV 的轮廓算法
# sys.setrecursionlimit(100000) 

class ScreenAreaSelector:
    """全屏透明遮罩，用于框选区域 (保持不变)"""
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

class AutoDrawApp:
    def __init__(self, root):
        self.root = root
        self.root.title("喜茶喜贴DIY助手")
        self.root.geometry("1000x750")
        
        # --- 核心变量 ---
        self.src_img = None       # 原始图像
        self.processed_preview = None 
        self.final_paths = []     # 最终的路径列表 [[(x,y), (x,y)...], [...]]
        self.target_area = None
        self.is_running = False
        self.drawing_thread = None
        self.debounce_timer = None
        
        # --- UI 布局 ---
        ctrl_frame = tk.Frame(root, pady=10, bg="#f0f0f0")
        ctrl_frame.pack(side=tk.TOP, fill=tk.X)
        
        tk.Button(ctrl_frame, text="1. 上传图片", command=self.load_image, bg="white").pack(side=tk.LEFT, padx=10)
        
        param_frame = tk.Frame(ctrl_frame, bg="#f0f0f0")
        param_frame.pack(side=tk.LEFT, padx=10)

        # 1. 边缘检测阈值 (Canny)
        tk.Label(param_frame, text="边缘细节:", bg="#f0f0f0", font=("Arial", 8)).grid(row=0, column=0)
        self.scale_canny = tk.Scale(param_frame, from_=255, to=10, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=0, length=100)
        self.scale_canny.set(100)
        self.scale_canny.grid(row=0, column=1)

        # 2. 忽略短线
        tk.Label(param_frame, text="忽略短线:", bg="#f0f0f0", font=("Arial", 8)).grid(row=0, column=2)
        self.scale_min_len = tk.Scale(param_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=1, length=100)
        self.scale_min_len.set(10) 
        self.scale_min_len.grid(row=0, column=3)

        # 3. 连接距离 (优化路径)
        tk.Label(param_frame, text="自动连接:", bg="#f0f0f0", font=("Arial", 8)).grid(row=1, column=0)
        self.scale_connect = tk.Scale(param_frame, from_=0, to=50, orient=tk.HORIZONTAL, command=self.trigger_update, showvalue=1, length=100)
        self.scale_connect.set(5) 
        self.scale_connect.grid(row=1, column=1)
        
        # 4. 笔画速度
        tk.Label(param_frame, text="笔画间隔(s):", bg="#f0f0f0", font=("Arial", 8)).grid(row=1, column=2)
        self.scale_delay = tk.Scale(param_frame, from_=0.0, to=0.5, resolution=0.01, orient=tk.HORIZONTAL, length=100)
        self.scale_delay.set(0.01) # 默认稍快一点，因为代码优化了
        self.scale_delay.grid(row=1, column=3)

        tk.Button(ctrl_frame, text="2. 框选区域", command=self.select_screen_area, bg="#b3e5fc").pack(side=tk.LEFT, padx=20)

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

    def load_image(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.jpg *.png *.bmp *.jpeg")])
        if not path: return
        img = cv2.imread(path)
        if img is None: return
        
        # 预处理：调整大小，保持比例，限制最大边长
        h, w = img.shape[:2]
        max_dim = 800
        if w > max_dim or h > max_dim:
            scale = max_dim / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = cv2.resize(img, (new_w, new_h))
            
        self.src_img = img # 保持彩色以便后续可能的扩展
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
        
        # 1. 转灰度
        gray = cv2.cvtColor(self.src_img, cv2.COLOR_BGR2GRAY)
        
        # 2. Canny 边缘检测 (比简单的阈值二值化效果好得多，能提取轮廓线条)
        canny_thresh = self.scale_canny.get()
        # Canny 需要两个阈值，这里动态计算
        edges = cv2.Canny(gray, canny_thresh // 2, canny_thresh)
        
        # 3. 查找轮廓 (这是替代像素遍历递归的核心)
        # RETR_LIST: 提取所有轮廓，不建立层级
        # CHAIN_APPROX_SIMPLE: 仅保留端点 (节省内存)，如果需要圆滑曲线可以用 CHAIN_APPROX_NONE
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        
        # 4. 转换 OpenCV 轮廓格式为点列表 [[(x,y)...], ...]
        raw_paths = []
        for cnt in contours:
            # cnt shape is (N, 1, 2) -> convert to [(x,y), (x,y)...]
            pts = cnt.reshape(-1, 2).tolist()
            raw_paths.append(pts)

        # 5. 路径优化 (合并距离近的线段)
        self.final_paths = self.optimize_paths(raw_paths)
        
        # 6. 生成预览图
        h, w = edges.shape
        preview = np.zeros((h, w), dtype=np.uint8)
        
        # 在预览图上画出最终路径
        for path in self.final_paths:
            if len(path) > 1:
                pts = np.array(path, np.int32).reshape((-1, 1, 2))
                cv2.polylines(preview, [pts], False, (255), 1)
        
        self.update_canvas_preview(preview)
        self.lbl_status.config(text=f"计算完成: 提取出 {len(self.final_paths)} 条笔画")

    def optimize_paths(self, raw_paths):
        """简单的贪婪算法合并路径"""
        min_len = self.scale_min_len.get()
        connect_dist_sq = self.scale_connect.get() ** 2
        
        # 过滤过短的噪点
        valid_paths = [p for p in raw_paths if len(p) > min_len]
        if not valid_paths: return []

        merged_paths = []
        
        while valid_paths:
            current_path = valid_paths.pop(0)
            
            while True:
                # 寻找与 current_path 终点最近的起点
                end_pt = current_path[-1]
                best_idx = -1
                min_d = float('inf')
                should_reverse = False
                
                # 只搜索前200个以提高性能（如果路径极多）
                search_limit = min(len(valid_paths), 200)
                
                for i in range(search_limit):
                    p = valid_paths[i]
                    # 检查 p 的起点
                    d1 = (p[0][0] - end_pt[0])**2 + (p[0][1] - end_pt[1])**2
                    # 检查 p 的终点 (也许可以反向连接)
                    d2 = (p[-1][0] - end_pt[0])**2 + (p[-1][1] - end_pt[1])**2
                    
                    curr_min = min(d1, d2)
                    if curr_min < min_d:
                        min_d = curr_min
                        best_idx = i
                        should_reverse = (d2 < d1)
                
                if best_idx != -1 and min_d <= connect_dist_sq:
                    # 合并
                    next_path = valid_paths.pop(best_idx)
                    if should_reverse:
                        next_path.reverse()
                    current_path.extend(next_path)
                else:
                    break # 找不到够近的，结束这一笔
            
            merged_paths.append(current_path)
            
        return merged_paths

    def update_canvas_preview(self, img_data):
        h, w = img_data.shape
        c_w, c_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if c_w < 10: c_w, c_h = 800, 500
        
        ratio = min(c_w/w, c_h/h) * 0.95
        new_w, new_h = int(w*ratio), int(h*ratio)
        
        resized = cv2.resize(img_data, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        resized = cv2.bitwise_not(resized) # 黑底白线转白底黑线方便查看
        
        self.tk_img = ImageTk.PhotoImage(Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(c_w//2, c_h//2, image=self.tk_img)

    def select_screen_area(self):
        self.root.withdraw()
        self.root.after(200, lambda: ScreenAreaSelector(self.root, self.on_area_selected))

    def on_area_selected(self, area):
        self.root.deiconify()
        self.target_area = area
        self.lbl_status.config(text="区域已锁定。请切换到画图软件，按 [F9] 开始绘制。")

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

    def _drawing_process(self):
        stroke_delay = self.scale_delay.get()
        # 获取图片尺寸
        img_h, img_w = self.src_img.shape[:2] # 使用原始比例
        
        tx, ty, tw, th = self.target_area
        
        # 计算缩放比例，保持纵横比
        scale = min(tw / img_w, th / img_h)
        # 居中偏移量
        offset_x = tx + (tw - img_w * scale) / 2
        offset_y = ty + (th - img_h * scale) / 2
        
        pyautogui.FAILSAFE = True
        # 极低的基础延时，主要靠我们的手动 sleep 控制
        pyautogui.PAUSE = 0.005 

        self.lbl_status.config(text=">>> 正在绘制中... 按 [F10] 停止 <<<")
        
        try:
            total_paths = len(self.final_paths)
            for i, path in enumerate(self.final_paths):
                if not self.is_running: break
                
                # 转换坐标
                screen_points = []
                for px, py in path:
                    sx = int(offset_x + px * scale)
                    sy = int(offset_y + py * scale)
                    screen_points.append((sx, sy))
                
                if len(screen_points) < 2: continue

                # --- 稳定性绘制逻辑 ---
                
                # 1. 移动到起点
                start_x, start_y = screen_points[0]
                pyautogui.keyUp('shift') # 防止意外连选
                pyautogui.mouseUp()
                pyautogui.moveTo(start_x, start_y)
                
                # 2. 停顿，等待鼠标归位
                time.sleep(0.02) 

                # 3. 下笔，给予软件反应时间
                pyautogui.mouseDown()
                time.sleep(0.05) # 关键延时：防漏笔

                # 4. 绘制路径
                # 优化：如果是直线，其实不需要每个点都走，但在Canny模式下点很密集
                # 我们每隔几个点走一次，提高速度，除非是拐角
                # 这里为了简单直接遍历，因为pyautogui底层有处理
                
                # 提高速度：使用 dragTo 还是 moveTo+mouseDown?
                # 实测 moveTo 配合 mouseDown 状态更可控
                
                # 抽稀点，避免每1像素移动一次导致卡顿
                step = 2 
                for j in range(1, len(screen_points), step):
                    if not self.is_running: break
                    x, y = screen_points[j]
                    pyautogui.moveTo(x, y)
                
                # 确保终点被画到
                end_x, end_y = screen_points[-1]
                pyautogui.moveTo(end_x, end_y)
                
                # 5. 抬笔前的停顿
                time.sleep(0.02) # 关键延时：确保最后一笔画完
                pyautogui.mouseUp()
                
                if i % 10 == 0: 
                    print(f"Drawing path {i}/{total_paths}")
                
                # 笔画间的额外休息
                if stroke_delay > 0:
                    time.sleep(stroke_delay)
                
        except Exception as e:
            print(f"Error: {e}")
            self.lbl_status.config(text=f"错误: {e}")
        finally:
            pyautogui.mouseUp()
            self.is_running = False
            self.lbl_status.config(text="绘制结束。")

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoDrawApp(root)
    root.mainloop()

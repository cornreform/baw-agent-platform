#!/usr/bin/env python3
"""
Slide Content Input System
接受用家輸入 slide content（文字 + 圖表需求）
"""

import json
from datetime import datetime

class SlideContentInput:
    """接受用家輸入 slide content"""
    
    def __init__(self):
        self.slides = []
        self.chart_types = [
            "bar", "column", "line", "pie", "area", 
            "scatter", "radar", "funnel"
        ]
    
    def get_slide_type(self):
        """選擇 slide 類型"""
        print("\n=== 選擇 Slide 類型 ===")
        print("1. 文字內容 (Text)")
        print("2. 圖表 (Chart)")
        print("3. 文字 + 圖表 (Text + Chart)")
        
        while True:
            choice = input("\n選擇 (1-3): ").strip()
            if choice in ["1", "2", "3"]:
                types = {"1": "text", "2": "chart", "3": "text_chart"}
                return types[choice]
            print("請輸入 1-3")
    
    def get_text_content(self):
        """取得文字內容"""
        print("\n=== 輸入文字內容 ===")
        title = input("標題: ").strip()
        
        print("\n輸入內容 (完成後輸入 END):")
        lines = []
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        
        content = "\n".join(lines)
        return {"title": title, "content": content}
    
    def get_chart_requirements(self):
        """取得圖表需求"""
        print("\n=== 輸入圖表需求 ===")
        
        # 選擇圖表類型
        print("\n可選圖表類型:")
        for i, ct in enumerate(self.chart_types, 1):
            print(f"{i}. {ct}")
        
        while True:
            try:
                idx = int(input("\n選擇圖表類型: ")) - 1
                if 0 <= idx < len(self.chart_types):
                    chart_type = self.chart_types[idx]
                    break
            except ValueError:
                pass
            print("請輸入有效編號")
        
        # 圖表標題
        chart_title = input("圖表標題: ").strip()
        
        # 輸入數據
        print("\n輸入數據 (格式: 標籤,數值)，完成後輸入 END")
        data_points = []
        while True:
            entry = input()
            if entry.strip().upper() == "END":
                break
            try:
                label, value = entry.split(",")
                data_points.append({
                    "label": label.strip(),
                    "value": float(value.strip())
                })
            except ValueError:
                print("格式錯誤，請使用: 標籤,數值")
        
        return {
            "chart_type": chart_type,
            "title": chart_title,
            "data": data_points
        }
    
    def add_slide(self):
        """新增一個 slide"""
        print("\n" + "="*50)
        print(f"=== 新增 Slide #{len(self.slides) + 1} ===")
        
        slide_type = self.get_slide_type()
        slide_data = {"type": slide_type}
        
        if slide_type in ["text", "text_chart"]:
            slide_data["text"] = self.get_text_content()
        
        if slide_type in ["chart", "text_chart"]:
            slide_data["chart"] = self.get_chart_requirements()
        
        self.slides.append(slide_data)
        print("\n✓ Slide 已新增")
    
    def run(self):
        """執行輸入系統"""
        print("="*50)
        print("  PowerPoint Slide Content Input System")
        print("  接受用家輸入 slide content（文字 + 圖表需求）")
        print("="*50)
        
        while True:
            print(f"\n目前已有 {len(self.slides)} 個 slides")
            print("1. 新增 slide")
            print("2. 顯示所有 slides")
            print("3. 儲存並離開")
            
            choice = input("\n選擇: ").strip()
            
            if choice == "1":
                self.add_slide()
            elif choice == "2":
                self.show_slides()
            elif choice == "3":
                self.save_and_exit()
                break
    
    def show_slides(self):
        """顯示所有 slides"""
        print("\n" + "="*50)
        print("=== 所有 Slides ===")
        for i, slide in enumerate(self.slides, 1):
            print(f"\n--- Slide #{i} ({slide['type']}) ---")
            print(json.dumps(slide, ensure_ascii=False, indent=2))
    
    def save_and_exit(self):
        """儲存並離開"""
        filename = f"slide_content_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.slides, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 已儲存至: {filename}")
        print(f"✓ 共 {len(self.slides)} 個 slides")


if __name__ == "__main__":
    app = SlideContentInput()
    app.run()
#!/usr/bin/env python3
"""
Chart Generator using Matplotlib
生成圖表並儲存為 PNG 檔案
"""

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import json
import sys
import os

# 設置中文字體
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'SimHei', 'Noto Sans CJK SC']
plt.rcParams['axes.unicode_minus'] = False


class ChartGenerator:
    """使用 matplotlib 生成圖表"""
    
    def __init__(self):
        self.chart_types = {
            "bar": self.create_bar_chart,
            "column": self.create_column_chart,
            "line": self.create_line_chart,
            "pie": self.create_pie_chart,
            "area": self.create_area_chart,
            "scatter": self.create_scatter_chart,
            "radar": self.create_radar_chart,
            "funnel": self.create_funnel_chart,
        }
    
    def create_bar_chart(self, data, title, output_path):
        """水平條形圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.barh(labels, values, color='steelblue')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Value', fontsize=12)
        
        for i, v in enumerate(values):
            ax.text(v + max(values)*0.01, i, f'{v}', va='center')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_column_chart(self, data, title, output_path):
        """柱狀圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.bar(labels, values, color='coral')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel('Value', fontsize=12)
        ax.set_xlabel('Category', fontsize=12)
        
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{height}', ha='center', va='bottom')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_line_chart(self, data, title, output_path):
        """折線圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(labels, values, marker='o', linewidth=2, markersize=8, color='green')
        ax.fill_between(labels, values, alpha=0.3, color='green')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel('Value', fontsize=12)
        ax.set_xlabel('Category', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_pie_chart(self, data, title, output_path):
        """圓餅圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(8, 8))
        colors = ['#ff9999', '#66b3ff', '#99ff99', '#ffcc99', '#ff99cc', '#99ccff']
        explode = [0.05] * len(values)
        
        ax.pie(values, labels=labels, autopct='%1.1f%%', 
               colors=colors[:len(values)], explode=explode,
               shadow=True, startangle=90)
        ax.set_title(title, fontsize=14, fontweight='bold')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_area_chart(self, data, title, output_path):
        """面積圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.fill_between(labels, values, alpha=0.4, color='purple')
        ax.plot(labels, values, marker='o', linewidth=2, color='purple')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel('Value', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_scatter_chart(self, data, title, output_path):
        """散點圖"""
        x_vals = [i for i, d in enumerate(data)]
        y_vals = [d["value"] for d in data]
        labels = [d["label"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(x_vals, y_vals, s=100, c='orange', alpha=0.7)
        
        for i, label in enumerate(labels):
            ax.annotate(label, (x_vals[i], y_vals[i]), 
                      textcoords="offset points", xytext=(0,10), ha='center')
        
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel('Value', fontsize=12)
        ax.set_xlabel('Index', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_radar_chart(self, data, title, output_path):
        """雷達圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        #  закрываем фигуру
        values += values[:1]
        angles = [i * 2 * 3.14159 / len(labels) for i in range(len(labels) + 1)]
        
        fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection='polar'))
        ax.plot(angles, values, 'o-', linewidth=2, color='teal')
        ax.fill(angles, values, alpha=0.3, color='teal')
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def create_funnel_chart(self, data, title, output_path):
        """漏斗圖"""
        labels = [d["label"] for d in data]
        values = [d["value"] for d in data]
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        #  создаём воронку
        y_pos = range(len(labels))
        max_val = max(values)
        
        for i, (label, val) in enumerate(zip(labels, values)):
            width = val / max_val
            ax.barh(i, width, left=(1-width)/2, color=plt.cm.Blues(val/max_val + 0.3))
            ax.text(0.5, i, f'{label}: {val}', ha='center', va='center', 
                   fontsize=10, fontweight='bold')
        
        ax.set_yticks([])
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlim(0, 1)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        return output_path
    
    def generate(self, chart_type, title, data, output_path):
        """生成圖表"""
        if chart_type not in self.chart_types:
            raise ValueError(f"Unknown chart type: {chart_type}")
        
        return self.chart_types[chart_type](data, title, output_path)


def usage():
    """顯示使用說明"""
    print("""
Chart Generator - 使用 matplotlib 生成圖表 PNG

使用方法:
    python3 chart_generator.py <chart_type> <title> "<label,value;label,value;...>" [output_file]

範例:
    python3 chart_generator.py bar "Sales Report" "Q1,100;Q2,150;Q3,200;Q4,180"
    python3 chart_generator.py pie "Market Share" "Product A,30;Product B,45;Product C,25"
    python3 chart_generator.py line "Growth Trend" "Jan,10;Feb,20;Mar,35;Apr,50" growth.png

可選圖表類型:
    bar, column, line, pie, area, scatter, radar, funnel
""")


def main():
    generator = ChartGenerator()
    
    #  如果沒有參數，顯示範例
    if len(sys.argv) < 2:
        #  產生範例圖表
        sample_data = [
            {"label": "Q1", "value": 100},
            {"label": "Q2", "value": 150},
            {"label": "Q3", "value": 200},
            {"label": "Q4", "value": 180},
        ]
        
        output = generator.generate(
            "column",
            "Quarterly Sales Report",
            sample_data,
            "chart_sample.png"
        )
        print(f"✓ Sample chart created: {output}")
        usage()
        return
    
    #  解析參數
    chart_type = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "Chart"
    data_str = sys.argv[3] if len(sys.argv) > 3 else "A,10;B,20;C,30"
    output_path = sys.argv[4] if len(sys.argv) > 4 else f"chart_{chart_type}.png"
    
    #  解析數據
    data = []
    for item in data_str.split(";"):
        try:
            label, value = item.split(",")
            data.append({"label": label.strip(), "value": float(value.strip())})
        except ValueError:
            print(f"Error parsing: {item}")
            continue
    
    if not data:
        print("No valid data provided")
        return
    
    #  生成圖表
    try:
        output = generator.generate(chart_type, title, data, output_path)
        print(f"✓ Chart created: {output}")
    except Exception as e:
        print(f"Error: {e}")
        usage()


if __name__ == "__main__":
    main()
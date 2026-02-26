import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d import Axes3D

# --- 1. 数据准备 ---
x = np.linspace(-20, 20, 100)
y = np.linspace(-20, 20, 100)
X, Y = np.meshgrid(x, y)

# 定义曲面高度
Z_barren = 0.0001 * (X**4 + Y**4)
Z_barren = np.clip(Z_barren, 0, 50)

Z_quadratic = 0.05 * (X**2 + Y**2)
Z_quadratic = np.clip(Z_quadratic, 0, 50)

# --- 2. 小球起点与轨迹计算 ---
start_x, start_y = -12, -8
z_start_barren = 0.0001 * (start_x**4 + start_y**4)
z_start_quad = 0.05 * (start_x**2 + start_y**2)

# 右图轨迹：顺畅滑落
t_quad = np.linspace(0, 1, 50)
traj_x_quad = start_x * np.exp(-4 * t_quad)
traj_y_quad = start_y * np.exp(-4 * t_quad)
traj_z_quad = 0.05 * (traj_x_quad**2 + traj_y_quad**2) + 0.5

# (左图轨迹数据计算部分保留，但后面不画它了)
np.random.seed(10)
t_barren = np.linspace(0, 1, 40)
scribble_scale = 0.15
traj_x_barren = start_x + np.random.normal(0, scribble_scale, 40).cumsum()
traj_y_barren = start_y + np.random.normal(0, scribble_scale, 40).cumsum()
traj_z_barren = 0.0001 * (traj_x_barren**4 + traj_y_barren**4) + 0.6


# --- 3. 开始绘图 ---
fig = plt.figure(figsize=(16, 8))

# ================= 左图：贫瘠高原 (Barren Plateau) =================
ax1 = fig.add_subplot(121, projection='3d')
ax1.plot_surface(X, Y, Z_barren, cmap='jet', rstride=2, cstride=2, linewidth=0, alpha=0.7)

# 【关键修改】这行画轨迹的代码被注释掉了，左图不再显示黑线
# ax1.plot(traj_x_barren, traj_y_barren, traj_z_barren, color='black', linestyle='-', linewidth=1.5, zorder=50)

# 2. 小黑球
ax1.scatter(start_x, start_y, z_start_barren + 1, color='#222222', s=250, edgecolors='white', alpha=1.0, depthshade=False, zorder=100)

# 3. 短粗胖的受阻箭头 (保持不变)
ax1.quiver(start_x, start_y, z_start_barren + 2, -start_x, -start_y, -z_start_barren, 
           length=5, color='black', linewidth=2.5, arrow_length_ratio=0.6, normalize=True, zorder=150)

# 悬浮红叉
ax1.text(0, 0, 45, 'X', color='#CC0000', fontsize=60, fontweight='bold', ha='center', zorder=200)

ax1.view_init(elev=35, azim=-60)
ax1.set_zlim([0, 50])
ax1.set_xlabel('State ($z_1$)', fontsize=12, labelpad=10)
ax1.set_ylabel('State ($z_2$)', fontsize=12, labelpad=10)
ax1.set_zlabel('Energy ($E$)', fontsize=12, labelpad=10)


# ================= 右图：二次碗 (Quadratic Bowl) =================
ax2 = fig.add_subplot(122, projection='3d')
ax2.plot_surface(X, Y, Z_quadratic, cmap='jet', rstride=2, cstride=2, linewidth=0, alpha=0.7)

# 1. 顺畅滑落的轨迹线 (右图保留)
ax2.plot(traj_x_quad, traj_y_quad, traj_z_quad, color='black', linestyle='--', linewidth=2, zorder=50)

# 2. 小黑球
ax2.scatter(start_x, start_y, z_start_quad + 1, color='#222222', s=250, edgecolors='white', alpha=1.0, depthshade=False, zorder=100)

# 3. 强烈的梯度箭头 (细长、锋利)
ax2.quiver(start_x, start_y, z_start_quad + 2, -start_x, -start_y, -z_start_quad, 
           length=12, color='black', linewidth=4, arrow_length_ratio=0.2, normalize=True, zorder=150)

# 悬浮绿对勾
ax2.text(0, 0, 45, '✓', color='#008000', fontsize=60, fontweight='bold', ha='center', zorder=200)

ax2.view_init(elev=35, azim=-60)
ax2.set_zlim([0, 50])
ax2.set_xlabel('State ($z_1$)', fontsize=12, labelpad=10)
ax2.set_ylabel('State ($z_2$)', fontsize=12, labelpad=10)
ax2.set_zlabel('Energy ($E$)', fontsize=12, labelpad=10)


# ================= 全局修饰与排版 =================
plt.figtext(0.5, 0.96, "Figure 2: Dynamical Landscape Smoothing (3D View)", ha='center', fontsize=16, fontweight='bold')
plt.figtext(0.28, 0.86, "(a) Original High-order\n(Barren Plateau)", ha='center', fontsize=14)
plt.figtext(0.72, 0.86, "(b) SymLQA Transformed\n(Quadratic Bowl)", ha='center', fontsize=14)
plt.figtext(0.28, 0.05, "Barren Plateau ($z^3$-like)", ha='center', fontsize=14)
plt.figtext(0.72, 0.05, "Quadratic Bowl ($z$) - Converged", ha='center', fontsize=14)

# 中间的大黑箭头
plt.annotate('', xy=(0.53, 0.5), xytext=(0.47, 0.5), xycoords='figure fraction',
             arrowprops=dict(facecolor='black', shrink=0.0, width=5, headwidth=20, headlength=20))

plt.subplots_adjust(left=0.05, right=0.95, bottom=0.15, top=0.82, wspace=0.15)

print("绘图完成！左图的黑色轨迹线已移除。窗口即将弹出...")
plt.show()
from pathlib import Path

p=Path(__file__).resolve().parent.parent/'pc'/'web'/'index.html'
s=p.read_text(encoding='utf-8')
a=s.index('<section><h2>定位板与拍摄')
b=s.index('</section>',a)+len('</section>')
s=s[:a]+'''<section><h2>Quest 3 深度扫描</h2><p>使用 Meta XR SDK、MRUK 和 OpenXR。无需定位板：用右手柄握把放置扫描框，框住物件，底部略高于桌面。</p><p><b>A</b> 开始 / 继续 · <b>B</b> 暂停<br><b>X</b> 完成并导出 · <b>Y</b> 重置面板位置<br>手柄射线 + 扳机点击面板按钮。</p><p>物件保持静止，缓慢绕行并改变高度。方位覆盖只表示拍摄方向；未看见的表面仍可能缺失。</p><p class="muted">使用环境深度融合实测表面，精度取决于 Quest 深度数据。透明、反光和过细结构可能缺失。模型保留未观测的孔洞。</p><details><summary>备用：定位板轮廓扫描</summary><p>定位板方案需要白色背景与有明显颜色差异的物件，只恢复轮廓外形。</p><a href="/board.svg" target="_blank">打开 A4 定位板 ↗</a></details></section>'''+s[b:]
s=s.replace('最近有效分割','最近有效测量').replace('绿色区域表示用于重建的物件轮廓','绿色区域表示本帧参与融合的表面')
s=s.replace('检查绿色轮廓是否包含完整物件。若明显错误，请结束本次扫描并重新开始，已剔除的几何不会自动恢复。','绿色显示参与融合的表面采样。检查扫描框是否包含桌面或背景；必要时结束本次扫描并重新放置扫描框。')
s=s.replace("+' 个三角面 · 单位：米 · 轮廓粗模'","+' 个三角面 · 单位：米 · '+(s.method==='depth-tsdf'?'Quest 深度融合':'轮廓粗模')")
p.write_text(s,encoding='utf-8')

"""Illustration 3D de couverture : crayon coupe en long, champ de temperature."""
import math, numpy as np, pyvista as pv

CAS = '/home/yann/pwr_rod_25kw_2y_v2/pwr_rod_25kw_2y_v2.foam'
R_PAST, R_GI, R_GE = 4.5, 4.565, 5.315      # mm
L = 58.0                                     # mm de crayon montres
pv.OFF_SCREEN = True

# ── profil radial mesure au plan median ────────────────────────────────────
rd = pv.OpenFOAMReader(CAS); rd.set_active_time_value(rd.time_values[-1])
rd.enable_all_cell_arrays()
im = rd.read()['internalMesh']
c = im.cell_centers().points
T = np.asarray(im.cell_data['T']).ravel()
r_mm, z_m = c[:, 0]*1000.0, c[:, 2]
zs = np.unique(np.round(z_m, 6)); zmed = zs[len(zs)//2]
sel = np.isclose(np.round(z_m, 6), zmed)
o = np.argsort(r_mm[sel])
r_prof, T_prof = r_mm[sel][o], T[sel][o]
T_de_r = lambda r: np.interp(r, r_prof, T_prof)

# ── geometrie : axe selon x, demi-cylindre (theta de 0 a pi) ───────────────
def secteur(r0, r1, nr, nth=150, nx=2):
    r  = np.linspace(r0, r1, nr)
    th = np.linspace(-math.pi, math.pi/2, nth)   # encoche ouverte vers le haut-avant
    x  = np.linspace(0.0, L, nx)
    R, TH, X = np.meshgrid(r, th, x, indexing='ij')
    g = pv.StructuredGrid(X, R*np.cos(TH), R*np.sin(TH))
    g['Temperature [K]'] = T_de_r(R.ravel(order='F'))
    return g

pastille = secteur(1e-3, R_PAST, 60)
gaine    = secteur(R_GI,  R_GE,  14)

clim = (float(T_prof.min()), float(T_prof.max()))
p = pv.Plotter(off_screen=True, window_size=(1900, 720))
p.set_background('white')
kw = dict(cmap='inferno', clim=clim, show_scalar_bar=False, smooth_shading=True,
          ambient=.40, diffuse=.75, specular=.32, specular_power=22)
p.add_mesh(pastille, **kw)
p.add_mesh(gaine, **kw)

# la face plane de la coupe regarde le lecteur, le dos arrondi reste visible
foc = (L/2, 0, 0)
dist = math.hypot(R_GE, L/2) / math.tan(math.radians(15))
az, el = math.radians(-68), math.radians(30)
p.camera_position = [(foc[0] + dist*math.cos(az)*math.cos(el),
                      foc[1] + dist*math.sin(az)*math.cos(el),
                      foc[2] + dist*math.sin(el)), foc, (0, 0, 1)]
p.reset_camera(); p.camera.zoom(1.62)

# pas de barre de couleur : la legende de la page de titre porte les valeurs,
# et l'image doit tenir dans un bandeau de 4,5 cm
p.enable_anti_aliasing('ssaa')
p.screenshot('rapport/Figures/couverture_3d.png')
print(f"ecrit. T de {clim[0]:.0f} a {clim[1]:.0f} K")

# ── recadrage sur le contenu, marge fine ───────────────────────────────────
from PIL import Image, ImageChops
img = Image.open('rapport/Figures/couverture_3d.png').convert('RGB')
bbox = ImageChops.difference(img, Image.new('RGB', img.size, (255, 255, 255))).getbbox()
if bbox:
    m = 26
    bbox = (max(0, bbox[0]-m), max(0, bbox[1]-m),
            min(img.width, bbox[2]+m), min(img.height, bbox[3]+m))
    img.crop(bbox).save('rapport/Figures/couverture_3d.png')
    print(f"recadre : {bbox[2]-bbox[0]} x {bbox[3]-bbox[1]} px")

"""Carte thermique du crayon en fin de cycle, depuis le cas de reference."""
import numpy as np, pyvista as pv
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

CAS = '/home/yann/pwr_rod_25kw_2y_v2/pwr_rod_25kw_2y_v2.foam'
R_PAST, R_GAINE_INT, R_GAINE_EXT = 4.5, 4.565, 5.315   # mm
CFUEL, CCLAD = '#C1541F', '#1F5FA8'

rd = pv.OpenFOAMReader(CAS); rd.set_active_time_value(rd.time_values[-1])
rd.enable_all_cell_arrays()
im = rd.read()['internalMesh']
c = im.cell_centers().points
T = np.asarray(im.cell_data['T']).ravel()
r_mm, z_m = c[:, 0] * 1000.0, c[:, 2]
past, gaine = r_mm < R_PAST, r_mm > R_GAINE_INT

def grille(masque):
    rr = np.unique(np.round(r_mm[masque], 6)); zz = np.unique(np.round(z_m[masque], 6))
    G = np.full((len(zz), len(rr)), np.nan)
    ir = {v: i for i, v in enumerate(rr)}; iz = {v: i for i, v in enumerate(zz)}
    for R, Z, t in zip(np.round(r_mm[masque], 6), np.round(z_m[masque], 6), T[masque]):
        G[iz[Z], ir[R]] = t
    return rr, zz, G

norm = Normalize(vmin=T.min(), vmax=T.max()); cmap = plt.get_cmap('inferno')
z_all = np.unique(np.round(z_m, 6)); z_med = z_all[len(z_all)//2]
sel = np.isclose(np.round(z_m, 6), z_med)

fig = plt.figure(figsize=(13.6, 5.9))
gs = fig.add_gridspec(1, 4, width_ratios=[.95, .045, .95, 1.15], wspace=.30)

# ── (a) coupe axiale-radiale ───────────────────────────────────────────────
ax = fig.add_subplot(gs[0, 0])
for m in (past, gaine):
    rr, zz, G = grille(m)
    ax.pcolormesh(rr, zz, G, cmap=cmap, norm=norm, shading='nearest')
ax.axvspan(R_PAST, R_GAINE_INT, color='0.88', zorder=3)
ax.set_xlim(0, R_GAINE_EXT); ax.set_ylim(0, 3.2)
ax.set_xlabel("Radius [mm]   (scale exaggerated $\\times$600)", fontsize=9.5)
ax.set_ylabel("Axial position [m]", fontsize=9.5)
ax.set_title("(a) Axial–radial section", fontsize=10.5, weight='bold')
ax.tick_params(labelsize=8.5)
ax.annotate("pellet", xy=(2.2, .12), color='w', ha='center', fontsize=9, weight='bold')
ax.text(4.94, 1.6, "cladding", color='w', fontsize=8, weight='bold',
        rotation=90, ha='center', va='center', zorder=5)
ax.annotate("gap", xy=(R_PAST + .035, 2.62), xytext=(3.35, 2.62), fontsize=8.5,
            color='w', va='center', ha='right', weight='bold',
            arrowprops=dict(arrowstyle='->', color='w', lw=.8))

cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap),
                  cax=fig.add_subplot(gs[0, 1]))
cb.set_label("Temperature [K]", fontsize=9.5); cb.ax.tick_params(labelsize=8.5)

# ── (b) coupe transversale ─────────────────────────────────────────────────
ax2 = fig.add_subplot(gs[0, 2])
th = np.linspace(0, 2*np.pi, 240)
for m in (past, gaine):
    s = sel & m; o = np.argsort(r_mm[s])
    rad, temp = r_mm[s][o], T[s][o]
    RR, TH = np.meshgrid(rad, th)
    ax2.pcolormesh(RR*np.cos(TH), RR*np.sin(TH), np.tile(temp, (len(th), 1)),
                   cmap=cmap, norm=norm, shading='nearest')
for rad, col in ((R_PAST, 'w'), (R_GAINE_INT, 'w'), (R_GAINE_EXT, '0.6')):
    ax2.plot(rad*np.cos(th), rad*np.sin(th), color=col, lw=.9)
ax2.set_aspect('equal'); ax2.axis('off')
ax2.set_title(f"(b) Cross-section, z = {z_med:.2f} m", fontsize=10.5, weight='bold')

# ── (c) profil radial : la chute du jeu, lisible ───────────────────────────
ax3 = fig.add_subplot(gs[0, 3])
for m, col, lab in ((past, CFUEL, 'pellet'), (gaine, CCLAD, 'cladding')):
    s = sel & m; o = np.argsort(r_mm[s])
    ax3.plot(r_mm[s][o], T[s][o], '-o', ms=3, lw=1.6, color=col, label=lab)
ax3.axvspan(R_PAST, R_GAINE_INT, color='0.9', zorder=0)
t_past = T[sel & past][np.argsort(r_mm[sel & past])][-1]
t_gain = T[sel & gaine][np.argsort(r_mm[sel & gaine])][0]
ax3.annotate('', xy=(R_PAST + .03, t_past), xytext=(R_PAST + .03, t_gain),
             arrowprops=dict(arrowstyle='<->', color='k', lw=1.1))
ax3.annotate(f"$\\Delta T$ = {t_past - t_gain:.0f} K across the gap.\n"
             "An open gap would drop several\nhundred K : contact is established.",
             xy=(.15, 1120), xytext=(.15, 1120),
             fontsize=8.8, va='center', color='0.15')
ax3.set_xlabel("Radius [mm]", fontsize=9.5)
ax3.set_ylabel("Temperature [K]", fontsize=9.5)
ax3.set_title("(c) Radial profile at mid-plane", fontsize=10.5, weight='bold')
ax3.grid(alpha=.25, lw=.6); ax3.tick_params(labelsize=8.5)
ax3.legend(fontsize=9, frameon=False, loc='lower left')
ax3.set_xlim(0, R_GAINE_EXT)

fig.suptitle("Fuel rod temperature at end of cycle  ·  25 kW/m, two-year irradiation",
             fontsize=12.5, weight='bold', y=.99)
fig.savefig('rapport/Figures/carte_thermique.png', dpi=170,
            bbox_inches='tight', facecolor='white')
print(f"pellet centre {T[sel & past].max():.0f} K | pellet edge {t_past:.0f} K | "
      f"clad inner {t_gain:.0f} K | gap drop {t_past - t_gain:.0f} K")

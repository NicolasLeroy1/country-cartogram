"""Optional visual explanation of the growth process, using the real solver frames."""
import numpy as np
import shapely
import matplotlib.pyplot as plt
from PIL import Image
from .pipeline import add_geometry, country_color


def render_evolution(frames, rows, config, stem, initial_label='Geographic start'):
    bounds = np.array([shapely.total_bounds(f['geometries']) for f in frames])
    xmin, ymin = bounds[:, :2].min(axis=0)
    xmax, ymax = bounds[:, 2:].max(axis=0)
    padding = (xmax - xmin) * .03
    images = []
    for k, frame in enumerate(frames):
        fig, ax = plt.subplots(figsize=(12, 7), facecolor=config['background'], dpi=90)
        ax.set_facecolor(config['background'])
        for geometry, row in zip(frame['geometries'], rows):
            add_geometry(ax, geometry, country_color(row, config))
        ax.set_xlim(xmin - padding, xmax + padding); ax.set_ylim(ymin - padding, ymax + padding)
        ax.set_aspect('equal'); ax.axis('off')
        heading = initial_label if k == 0 else (
            'After settling' if k == len(frames) - 1 else f"Resizing: {frame['progress']:.0%}")
        fig.suptitle(heading, fontsize=19, color=config['ink'], weight='bold', y=.96)
        fig.text(.5, .04, 'Local polygon repulsion + long-range attraction within each cluster',
                 ha='center', fontsize=10, color='#4d6576')
        fig.canvas.draw()
        images.append(Image.fromarray(np.asarray(fig.canvas.buffer_rgba()).copy()).convert('RGB'))
        plt.close(fig)
    durations = [180] * len(images)
    durations[0] = 1200; durations[-1] = 2400
    images[0].save(stem.with_suffix('.growth.gif'), save_all=True, append_images=images[1:],
                   duration=durations, loop=0)
    chosen = [0, min(range(len(frames)), key=lambda i: abs(frames[i]['progress'] - .5)), len(frames)-1]
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), facecolor=config['background'])
    for ax, index, title in zip(axes, chosen, [initial_label, 'Halfway through resizing', 'After settling']):
        for geometry, row in zip(frames[index]['geometries'], rows):
            add_geometry(ax, geometry, country_color(row, config))
        ax.set_xlim(xmin - padding, xmax + padding); ax.set_ylim(ymin - padding, ymax + padding)
        ax.set_aspect('equal'); ax.axis('off'); ax.set_title(title, fontsize=13, color=config['ink'])
    fig.suptitle('Grow and shrink in place; let contact and attraction move the countries',
                 fontsize=19, color=config['ink'], weight='bold', y=.94)
    fig.savefig(stem.with_suffix('.growth.png'), dpi=160, facecolor=config['background'], bbox_inches='tight')
    plt.close(fig)

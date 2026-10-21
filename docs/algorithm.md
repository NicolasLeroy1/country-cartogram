# Design and algorithm notes

## The model

Each country is a translating rigid body with one or more retained polygon parts.
Rotation is locked. The final rendered area is proportional to its input value.
Continents are clusters, not rigid containers: all countries participate in the
same simulation, so attraction and contacts can move them across cluster borders.

Preprocessing projects Natural Earth outlines to Equal Earth, unwraps longitude
for country components across the date line, drops sufficiently tiny components,
and simplifies the coastlines. The retained area fraction is recorded. A common
scale normalizes the initial geographic map to the configured total area.

## Grow and shrink gradually

For positive quantities $q_i$, the final target area is

$$T_i = A_{\mathrm{total}}\frac{q_i}{\sum_j q_j}.$$

At growth stage $u\in[0,1]$, use $t=3u^2-2u^3$ and interpolate areas:

$$A_i(t)=(1-t)A_i(0)+tT_i.$$

The current outline is the final outline scaled by
$\sqrt{A_i(t)/T_i}$. Interpolating areas rather than linear dimensions avoids
confusing a doubled width with a doubled quantity. Forces and contact solving
remain active at every growth stage.

## Attraction and contacts

Bodies have mass proportional to final target area. For cluster $C$, its moving
center is

$$c_C=\frac{\sum_{i\in C}m_i x_i}{\sum_{i\in C}m_i}.$$

The unconstrained acceleration includes

$$a_i=k_g(g_i-x_i)+k_c(c_C-x_i).$$

Here $g_i$ denotes the geographic position anchor. Parent
links add equal-and-opposite forces with a gradually compacted preferred offset.
Velocity damping removes oscillation. With these mass weights, the cluster
forces sum to zero internally; geographic springs and contacts with other
clusters can still move a cluster's barycenter.

Pymunk/Chipmunk resolves short-range contacts. The default uses one convex hull
per retained land component, with a contact radius of half the requested gap
plus a numerical margin. All components of a country share one body and do not
collide with one another. The hulls contain the finer displayed polygons, making
the approximation conservative. No hull is constructed around a whole cluster.

## Remote islands and territories

Multipart countries begin with the retained components at their geographic
offsets. As growth progresses, remote offsets compact radially toward the main
part. The projected direction is retained, and final parts are kept disjoint.
Separate CSV records can use a `parent` code; these records retain independent
quantities and receive a parent-relative spring.

Intermediate components can cross while moving toward their insets. Therefore,
the strict area and separation guarantees apply to the audited final output,
not every intermediate animation frame. Parent-link directions are approximate
in the chosen projection; a map seam can also affect distant territory placement.

## Settling and independent validation

After target sizes are reached, attraction and repulsion continue together.
Convergence means 15 consecutive updates below `position_tolerance` in maximum
body displacement. An iteration limit prevents unbounded work and is explicitly
reported if reached. This numerical stopping rule does not prove a global optimum.

The final audit uses the actual displayed polygons, independently of the contact
engine. It checks validity, all pairwise overlaps and minimum distances, and
each country's area against its normalized input. Invalid geometry, overlaps,
area error, or an insufficient gap aborts export.

## What proved useful

| Choice | Why it matters |
| --- | --- |
| Geographic initialization and gradual resizing | Provides continuity from a recognizable world map |
| Cluster attraction active throughout growth | Groups countries while their sizes and contacts change |
| Coarse contact shapes, finer export geometry | Cuts collision work while preserving recognizable outlines |
| Small time step | Reduced persistent contact jitter in dense regions |
| Conservative component hulls | Let enclaves escape when the resized surrounding hole becomes too small |
| Independent final audit | Detects contact errors the physics engine might otherwise hide |
| Explicit diagnostics | Distinguishes reaching a tolerance from exhausting the iteration budget |

The default GDP run uses 693 convex collision pieces for 230 countries and
territories. A more detailed partition used roughly 24,000 pieces during
development. This comparison concerns geometry complexity, not a controlled
runtime benchmark.

`collision_mode: "decomposed"` retains concavities and holes using simplified
polygon partitions. It can make tighter layouts but may trap enclaves and
failed the GDP audit during development. It remains an experimental option.
The earlier two-stage continental packer is available as `layout_mode: "legacy"`
for comparison; it is not the default.

## Possible extensions

- Bounded affine or elastic deformation with an area constraint and shape penalty.
- Adaptive growth increments when a contact region becomes congested.
- Neighbor springs to make loss of geographic adjacency more expensive.
- A projection-aware treatment of parent links across the map seam.
- A spatial index for the final audit when scaling beyond country-sized datasets.

These are directions for further work, not claims about current functionality.

## References

- [Pymunk API](https://www.pymunk.org/en/latest/pymunk.html)
- [Shapely documentation](https://shapely.readthedocs.io/en/stable/manual.html)
- [Natural Earth Admin 0 countries](https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-admin-0-countries-2/)
- [Python packaging guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)

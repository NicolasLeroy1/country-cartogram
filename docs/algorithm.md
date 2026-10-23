# Design and algorithm notes

## The model

Each region is a translating rigid body with one or more retained polygon parts.
Rotation is locked. The final rendered area is proportional to its input value.
All regions share one simulation. Group attraction and contacts determine
their positions; continents can move relative to one another.

Map loading converts geographic boundaries to the chosen projection, or keeps
local coordinates. The geometry stage selects components and simplifies planar
polygons before simulation. It records the retained area fraction and normalizes
the initial map to the configured total area.

## Shape approximation

Before island placement or simulation, each retained projected polygon is
simplified within the `shape_error` budget (default 0.02). A candidate's area
and centroid are restored before testing

$$\frac{\operatorname{area}(P \mathbin{\triangle} P')}{\operatorname{area}(P)} \leq \text{shape_error}.$$

The adaptive search keeps the coarsest tested candidate within the budget,
preserving holes.
The same prepared shapes feed collision construction, all growth stages,
rendering, and continuation. The budget applies after island selection; dropped
parts and intentional inset movement are recorded separately.

`shape_error: 0` uses the fixed `simplify` tolerance. `simplify: 0` disables both
methods. Final area normalization still preserves the supplied quantities.

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

Bodies have mass proportional to final target area. For group $C$, its moving
center is

$$c_C=\frac{\sum_{i\in C}m_i x_i}{\sum_{i\in C}m_i}.$$

The unconstrained acceleration includes

$$a_i=k_g(g_i-x_i)+k_c(c_C-x_i).$$

Here $g_i$ denotes the geographic position anchor. Parent
links add equal-and-opposite forces with a gradually compacted preferred offset.
Velocity damping removes oscillation. With these mass weights, the group
forces sum to zero internally; geographic springs and contacts with other
groups can still move a group's barycenter.

Pymunk/Chipmunk resolves short-range contacts. The default uses one convex hull
per retained land component, expanded by a square of half the requested gap
plus a numerical margin. This conservative envelope avoids unreliable rounded
contacts on tiny islands. All components of a region share one body and do not
collide with one another. The hulls contain the finer displayed polygons, making
the approximation conservative. A second contact skin uses `group_gap` and
only collides with regions from other groups. The default gaps are 0.2 within
groups and 3.6 between groups. Repulsion is local, so distant groups are not
pushed apart continually. No hull is constructed around a whole group.
These are minimum gaps; conservative envelopes can leave more space at corners.

## Remote islands and territories

Multipart regions begin with the retained components at their geographic
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
each region's area against its normalized input. Invalid geometry, overlaps,
area error, or an insufficient gap aborts export.

`collision_mode: "decomposed"` partitions simplified concave polygons into
convex pieces. It can fit coastlines more closely but is slower and may trap
enclaves.

## References

- [Pymunk API](https://www.pymunk.org/en/latest/pymunk.html)
- [Shapely documentation](https://shapely.readthedocs.io/en/stable/manual.html)
- [Natural Earth Admin 0 countries](https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-admin-0-countries-2/)

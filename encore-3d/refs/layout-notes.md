# Layout notes (traced from floorplan-map-2026.png)

## Calibration

- The plan is mirror-symmetric about pixel column x = 400. South is on the left, North on the right.
- DJ booth centre = pixel (400, 548) = world origin.
- World X = (px - 400) * SCALE, world Y = (548 - py) * SCALE, SCALE = 0.13 m/px (estimate).
  With this scale the outdoor deck is about 98 m wide and 60 m deep, roughly 5,500 m2.
- Both cabana crescents are circular arcs centred on pixel (400, 480):
  lower cabanas r = 342 px, lower bungalows r = 330 px, upper cabanas r = 410 px,
  upper bungalows r = 395 px, back facade r = 440-452 px.

## Zones and table groups built

| Zone (collection) | Tables | Type property | Notes |
| --- | --- | --- | --- |
| Cabanas_Lower | 100-106 south, 107-113 north | `lower_cabana` | arc, angles 150 -> 125 deg (south), mirrored north |
| Cabanas_Upper | 200-206 south, 207-213 north | `upper_cabana` | second floor at z = 3.9 m, angles 149.5 -> 123 deg |
| Bungalows | 1-2 south, 3-4 north (lower); 5-6 south, 7-8 north (upper) | `bungalow` | with private plunge pool and deck |
| Pools | 400-409 south lobe, 410-423 north lobe | `lily_pad` | along the outer edges of each lobe |
| Pools | 600-605 | `water_couch` | centre basin |
| Daybeds | 300-305 south, 306-311 north | `daybed` | with umbrella |
| Daybeds | 700-716 | `casa_playa` | salmon "Casa Playa" legend entry: beds in front of the gaming pavilion, next to the Casa Playa restaurant shown on the Wynn property map |
| Beach_Couches | 500-534, 800-821 | `beach_couch` | 500s north/centre-right, 800s south-of-pool rows |
| Dance_Floor | 30-36 | `l_couch` | purple dance-floor section between pool and stage |
| Stage | 98, 99 | `stage_couch` | either side of the DJ booth |
| Gaming | G1-G8 | `gaming_table` | under the pavilion, in front of the gaming bar |
| Backstage | 1-13, 20-22 | `dance_couch_large/medium/small` | corridor behind the stage; numbering is approximate |

Fixed structures: gaming pavilion with gaming bar, pool bar, Ultra Bar, grill, service bar,
restrooms (south, north, backstage), outside entrance and lockers (south), inside entrance
(backstage), stage with DJ booth and truss, backstage corridor with long bar.

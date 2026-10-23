/* Shared static and temporal viewer. No per-frame geometry allocation. */
(async () => {
  "use strict";

  const perlinShader = `@@SURFACE@@`;

  const $ = (id) => document.getElementById(id),
    notice = $("notice");
  try {
    const THREE = CartogramThree;
    function bytes(s) {
      return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
    }
    if (!globalThis.DecompressionStream)
      throw new Error(
        "This viewer needs a browser with gzip DecompressionStream support.",
      );
    const stream = new Blob([bytes($("scene-data").textContent.trim())])
      .stream()
      .pipeThrough(new DecompressionStream("gzip"));
    const data = JSON.parse(await new Response(stream).text());
    const transforms = new Float32Array(bytes(data.transforms).buffer),
      count = data.regions.length,
      meshCount = data.meshes.length;
    const viewport = $("viewport"),
      scene = new THREE.Scene();
    scene.background = new THREE.Color("#f7f8f5");
    let renderedTriangles = 0,
      renderedDrawCalls = 0;
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      preserveDrawingBuffer: false,
    });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    viewport.prepend(renderer.domElement);
    renderer.domElement.setAttribute(
      "aria-label",
      "Interactive region cartogram",
    );
    renderer.domElement.setAttribute("role", "img");
    const b = data.bounds,
      span = Math.max(b[2] - b[0], b[3] - b[1]),
      cx = (b[0] + b[2]) / 2,
      cy = (b[1] + b[3]) / 2;
    const camera = new THREE.OrthographicCamera(
      -span,
      span,
      span,
      -span,
      span * 0.001,
      span * 30,
    );
    camera.up.set(0, 0, 1);
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.maxPolarAngle = Math.PI / 2 - 0.01;
    controls.minZoom = 0.15;
    controls.maxZoom = 80;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x74818a, 1.65));
    const light = new THREE.DirectionalLight(0xffffff, 1.2);
    light.position.set(-span * 0.3, span * 1.3, span * 1.2);
    scene.add(light);
    light.layers.enableAll();
    scene.children
      .filter((o) => o.isLight)
      .forEach((o) => o.layers.enableAll());
    const oitAvailable =
      data.mode === "3d" && renderer.extensions.has("EXT_color_buffer_float");
    const transparencyPass = { value: -1 };
    let opacity = data.config.opacity ?? 0.4;
    const ground = new THREE.GridHelper(span * 1.35, 24, 0xdce3de, 0xe4e9e5);
    ground.rotation.x = Math.PI / 2;
    ground.position.z = -span * 0.002;
    scene.add(ground);
    const meshes = [],
      solids = [],
      flats = [],
      flatMaterials = [],
      translucentMaterials = [],
      groups = new Map();
    const years = data.endpoints.map((i) => Number(data.frames[i].year)),
      calendar = years.every(
        (year, i) => Number.isFinite(year) && (i === 0 || year > years[i - 1]),
      ),
      marks = data.endpoints.map((frame, i) => ({
        frame,
        time: calendar ? years[i] : i,
        label: String(data.frames[frame].year),
      })),
      totals = data.observations.map((rows) =>
        rows.reduce((sum, row) => sum + row[0], 0),
      ),
      values = new Float64Array(count * 3),
      estimateWeights = new Float32Array(count),
      valueStatuses = Array(count).fill("observed"),
      valueNotes = Array(count).fill(""),
      baseColors = data.regions.map((c) => new THREE.Color(c.color)),
      estimatedColor = new THREE.Color("#a0a5a9");
    const frameTimes = new Float64Array(data.frameCount);
    frameTimes[0] = marks[0].time;
    for (let segment = 0; segment + 1 < marks.length; segment++) {
      const left = marks[segment],
        right = marks[segment + 1];
      let growthEnd = left.frame;
      for (let i = left.frame + 1; i < right.frame; i++) {
        if (data.frames[i].phase === "growth") growthEnd = i;
      }
      for (let i = left.frame + 1; i <= right.frame; i++) {
        // Resizing takes most of the year; a long numerical settling phase
        // must not squeeze all economic change into a brief visual jump.
        const fraction =
          growthEnd === left.frame
            ? (i - left.frame) / (right.frame - left.frame)
            : i <= growthEnd
              ? 0.8 * data.frames[i].progress
              : 0.8 + (0.2 * (i - growthEnd)) / (right.frame - growthEnd);
        frameTimes[i] = left.time + (right.time - left.time) * fraction;
      }
    }
    function statusAt(year, region) {
      return data.valueStatuses?.[String(year)]?.[region] || "observed";
    }
    function estimateAt(frame, region) {
      const from = frame.from ?? frame.year;
      return (
        Number(statusAt(from, region) !== "observed") * (1 - frame.progress) +
        Number(statusAt(frame.year, region) !== "observed") * frame.progress
      );
    }
    let time = marks[0].time,
      framePosition = 0,
      label = marks[0].label,
      observation = true,
      currentTotal = 0;
    let index = 0,
      playing = false,
      raf = null,
      last = 0,
      top = data.mode === "2d",
      hovered = null,
      pointer = null,
      hoverRequest = null;
    for (let i = 0; i < meshCount; i++) {
      const g = data.meshes[i],
        c = data.regions[g.region],
        vertices = new Float32Array(bytes(g.vertices).buffer),
        Index = g.indexBytes === 2 ? Uint16Array : Uint32Array;
      const solid = new THREE.BufferGeometry();
      solid.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
      solid.setIndex(
        new THREE.BufferAttribute(new Index(bytes(g.faces).buffer), 1),
      );
      solid.computeVertexNormals();
      solid.computeBoundingSphere();
      const flat = new THREE.BufferGeometry(),
        flatVertices = vertices.slice();
      for (let k = 2; k < flatVertices.length; k += 3) flatVertices[k] = 0;
      flat.setAttribute("position", new THREE.BufferAttribute(flatVertices, 3));
      flat.setIndex(
        new THREE.BufferAttribute(new Index(bytes(g.roofs).buffer), 1),
      );
      flat.computeVertexNormals();
      flat.computeBoundingSphere();
      const flatMaterial = new THREE.MeshStandardMaterial({
        color: c.color,
        roughness: 0.85,
        metalness: 0,
        flatShading: true,
        side: THREE.DoubleSide,
      });
      const outlineSpan = Math.max(
        ...[0, 1].map((axis) => {
          let lo = Infinity,
            hi = -Infinity;
          for (let k = axis; k < vertices.length; k += 3) {
            lo = Math.min(lo, vertices[k]);
            hi = Math.max(hi, vertices[k]);
          }
          return hi - lo;
        }),
      );
      const material =
        data.mode === "3d"
          ? new THREE.MeshStandardMaterial({
              color: c.color,
              roughness: 1,
              metalness: 0,
              flatShading: true,
              transparent: true,
              opacity,
              depthWrite: false,
              side: THREE.DoubleSide,
              forceSinglePass: true,
            })
          : flatMaterial;
      // Smooth one-octave gradient Perlin field perturbs normals only. There is
      // no displacement, tessellation, color speckle, or stochastic transparency.
      material.onBeforeCompile = (shader) => {
        shader.uniforms.oitPass = transparencyPass;
        shader.uniforms.surfaceNoise = { value: data.config.surface_noise };
        shader.uniforms.noiseFrequency = { value: data.config.noise_frequency };
        shader.uniforms.noiseWorldScale = {
          value: Math.max(outlineSpan, 0.001),
        };
        shader.vertexShader =
          "varying vec3 noisePosition;\n" + shader.vertexShader;
        shader.vertexShader = shader.vertexShader.replace(
          "#include <begin_vertex>",
          "#include <begin_vertex>\nnoisePosition=mat3(modelMatrix)*position;",
        );
        shader.fragmentShader = perlinShader + shader.fragmentShader;
        shader.fragmentShader = shader.fragmentShader.replace(
          "#include <normal_fragment_maps>",
          `#include <normal_fragment_maps>
       float fieldHeight=surfaceNoise*noiseWorldScale*.12*cartogramPerlin(noisePosition*noiseFrequency/noiseWorldScale);
       vec3 sx=dFdx(-vViewPosition),sy=dFdy(-vViewPosition);
       vec3 r1=cross(sy,normal),r2=cross(normal,sx);
       float determinant=dot(sx,r1)*faceDirection;
       vec3 gradient=sign(determinant)*(dFdx(fieldHeight)*r1+dFdy(fieldHeight)*r2);
       normal=normalize(max(abs(determinant),1e-20)*normal-gradient);
     `,
        );
        shader.fragmentShader = shader.fragmentShader.replace(
          "#include <lights_fragment_end>",
          "#include <lights_fragment_end>\nreflectedLight.directSpecular=vec3(0.0);reflectedLight.indirectSpecular=vec3(0.0);",
        );
        shader.fragmentShader =
          "uniform float oitPass;\n" + shader.fragmentShader;
        if (oitAvailable) {
          // Weighted accumulation and multiplicative revealage are commutative:
          // camera motion cannot flip whole-region transparency sorting.
          shader.fragmentShader = shader.fragmentShader.replace(
            "void main() {",
            "void main() {\nif(oitPass>.5){gl_FragColor=vec4(0.0,0.0,0.0,opacity);return;}",
          );
          shader.fragmentShader = shader.fragmentShader.replace(
            "#include <dithering_fragment>",
            `#include <dithering_fragment>
         if(oitPass>=0.0){
           float a=diffuseColor.a;
           float weight=.1+8.0*pow(1.0-gl_FragCoord.z,3.0);
           gl_FragColor=vec4(gl_FragColor.rgb*a*weight,a*weight);
         }
       `,
          );
        }
      };
      flatMaterials.push(flatMaterial);
      translucentMaterials.push(material);
      const mesh = new THREE.Mesh(solid, material);
      mesh.userData.index = g.region;
      mesh.matrixAutoUpdate = false;
      scene.add(mesh);
      meshes.push(mesh);
      solids.push(solid);
      flats.push(flat);
      if (!groups.has(c.group))
        groups.set(c.group, { color: c.color, visible: true });
    }
    let renderRequest = null;
    const opaqueTarget = oitAvailable
      ? new THREE.WebGLRenderTarget(1, 1)
      : null;
    const accumulationTarget = oitAvailable
      ? new THREE.WebGLRenderTarget(1, 1, {
          type: THREE.HalfFloatType,
          depthBuffer: false,
        })
      : null;
    const revealTarget = oitAvailable
      ? new THREE.WebGLRenderTarget(1, 1, { depthBuffer: false })
      : null;
    const compositeScene = new THREE.Scene(),
      compositeCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    if (oitAvailable) {
      const composite = new THREE.ShaderMaterial({
        depthTest: false,
        depthWrite: false,
        uniforms: {
          backdrop: { value: opaqueTarget.texture },
          accumulation: { value: accumulationTarget.texture },
          revealage: { value: revealTarget.texture },
        },
        vertexShader:
          "varying vec2 vUv; void main(){vUv=position.xy*.5+.5;gl_Position=vec4(position.xy,0.0,1.0);}",
        fragmentShader: `
     uniform sampler2D backdrop,accumulation,revealage;varying vec2 vUv;
     void main(){
       vec4 sum=texture2D(accumulation,vUv);
       float reveal=texture2D(revealage,vUv).r;
       vec3 color=mix(sum.rgb/max(sum.a,1e-5),texture2D(backdrop,vUv).rgb,reveal);
       gl_FragColor=vec4(color,1.0);
       #include <colorspace_fragment>
     }`,
      });
      compositeScene.add(
        new THREE.Mesh(new THREE.PlaneGeometry(2, 2), composite),
      );
    }
    function render() {
      renderRequest = null;
      if (!oitAvailable || top || opacity === 1) {
        transparencyPass.value = -1;
        camera.layers.enableAll();
        renderer.setRenderTarget(null);
        renderer.render(scene, camera);
        renderedTriangles = renderer.info.render.triangles;
        renderedDrawCalls = renderer.info.render.calls;
        return;
      }
      const background = scene.background;
      // The only opaque scene geometry is at/below z=0. Positive-height country
      // surfaces are in front of that plane for every allowed camera angle.
      camera.layers.set(0);
      renderer.setRenderTarget(opaqueTarget);
      renderer.setClearColor(background, 1);
      renderer.render(scene, camera);
      renderedTriangles = renderer.info.render.triangles;
      renderedDrawCalls = renderer.info.render.calls;
      scene.background = null;
      camera.layers.set(1);
      translucentMaterials.forEach((m) => {
        m.blending = THREE.CustomBlending;
        m.blendEquation = THREE.AddEquation;
        m.blendSrc = THREE.OneFactor;
        m.blendDst = THREE.OneFactor;
        m.depthTest = false;
      });
      transparencyPass.value = 0;
      renderer.setRenderTarget(accumulationTarget);
      renderer.setClearColor(0x000000, 0);
      renderer.render(scene, camera);
      renderedTriangles += renderer.info.render.triangles;
      renderedDrawCalls += renderer.info.render.calls;
      translucentMaterials.forEach((m) => {
        m.blendSrc = THREE.ZeroFactor;
        m.blendDst = THREE.OneMinusSrcAlphaFactor;
      });
      transparencyPass.value = 1;
      renderer.setRenderTarget(revealTarget);
      renderer.setClearColor(0xffffff, 1);
      renderer.render(scene, camera);
      renderedTriangles += renderer.info.render.triangles;
      renderedDrawCalls += renderer.info.render.calls;
      scene.background = background;
      camera.layers.enableAll();
      renderer.setRenderTarget(null);
      renderer.render(compositeScene, compositeCamera);
      renderedTriangles += renderer.info.render.triangles;
      renderedDrawCalls += renderer.info.render.calls;
    }
    function updateOpacity(value) {
      opacity = value;
      $("opacity-value").textContent = Math.round(value * 100) + "%";
      translucentMaterials.forEach((m) => {
        if (data.mode !== "3d") return;
        m.opacity = value;
        m.depthTest = true;
        m.depthWrite = value === 1;
        m.blending = THREE.NormalBlending;
      });
      invalidate();
    }
    function invalidate() {
      if (renderRequest === null) renderRequest = requestAnimationFrame(render);
    }
    controls.addEventListener("change", () => {
      hideTooltip();
      invalidate();
    });
    function resize() {
      const w = viewport.clientWidth,
        h = viewport.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      if (oitAvailable) {
        const pixelRatio = renderer.getPixelRatio();
        [opaqueTarget, accumulationTarget, revealTarget].forEach((target) =>
          target.setSize(
            Math.round(w * pixelRatio),
            Math.round(h * pixelRatio),
          ),
        );
      }
      camera.updateMatrixWorld(true);
      const points = [];
      for (const x of [b[0] - cx, b[2] - cx])
        for (const y of [b[1] - cy, b[3] - cy])
          for (const z of [0, top ? 0 : data.maxHeight])
            points.push(
              new THREE.Vector3(x, y, z).applyMatrix4(
                camera.matrixWorldInverse,
              ),
            );
      const xs = points.map((p) => p.x),
        ys = points.map((p) => p.y),
        left = Math.min(...xs),
        right = Math.max(...xs),
        bottom = Math.min(...ys),
        upper = Math.max(...ys),
        aspect = w / h;
      const halfY =
          Math.max(
            (upper - bottom) / 2,
            (right - left) / (2 * aspect),
            span * 0.02,
          ) * 1.12,
        halfX = halfY * aspect;
      camera.left = (left + right) / 2 - halfX;
      camera.right = (left + right) / 2 + halfX;
      camera.top = (bottom + upper) / 2 + halfY;
      camera.bottom = (bottom + upper) / 2 - halfY;
      camera.updateProjectionMatrix();
      invalidate();
    }
    new ResizeObserver(resize).observe(viewport);
    function view(isTop) {
      top = isTop;
      controls.enableRotate = !isTop;
      controls.screenSpacePanning = true;
      controls.mouseButtons.LEFT = isTop ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
      controls.touches.ONE = isTop ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
      $("controls-help").textContent =
        (isTop ? "Drag to pan" : "Drag to rotate · right drag to pan") +
        " · scroll to zoom · hover for values · click a group to hide it.";
      $("opacity-label").hidden = isTop || data.mode !== "3d";
      ground.visible = !isTop && data.mode === "3d";
      camera.zoom = 1;
      controls.target.set(0, 0, data.maxHeight * (isTop ? 0 : 0.15));
      camera.position.set(
        isTop ? 0 : span * 0.32,
        isTop ? -span * 0.0001 : -span * 1.3,
        span * (isTop ? 2 : 1.15),
      );
      camera.updateProjectionMatrix();
      controls.update();
      $("top").setAttribute("aria-pressed", String(top));
      $("view3d").setAttribute("aria-pressed", String(!top));
      resize();
      draw(framePosition);
    }
    const numberFormat = new Intl.NumberFormat(undefined, {
      maximumSignificantDigits: 5,
    });
    function fmt(v) {
      return numberFormat.format(v);
    }
    function hideTooltip() {
      hovered = null;
      $("tooltip").style.display = "none";
    }
    function tooltip(i) {
      const c = data.regions[i],
        regionValues = values.subarray(i * 3, i * 3 + 3),
        tip = $("tooltip");
      tip.replaceChildren();
      const title = document.createElement("strong");
      title.textContent = c.name + " (" + c.id + ")";
      tip.append(title);
      ["area_label", "height_label", "volume_label"].forEach((key, k) => {
        if (data.mode === "2d" && k > 0) return;
        const line = document.createElement("div");
        line.textContent = data.config[key] + ": " + fmt(regionValues[k]);
        tip.append(line);
      });
      if (data.normalization === "shares") {
        const line = document.createElement("div");
        line.textContent =
          fmt((100 * regionValues[0]) / currentTotal) + "% of included total";
        tip.append(line);
      }
      if (estimateWeights[i] > 1e-8) {
        const line = document.createElement("div");
        line.textContent =
          "Estimated · " +
          (valueNotes[i] || valueStatuses[i].replaceAll("_", " "));
        tip.append(line);
      }
      if (!observation) {
        const line = document.createElement("div");
        line.textContent = "Interpolated transition · not an observation";
        tip.append(line);
      }
      tip.style.display = "block";
      const rect = viewport.getBoundingClientRect();
      tip.style.left =
        Math.max(
          8,
          Math.min(
            pointer.x - rect.left + 16,
            rect.width - tip.offsetWidth - 8,
          ),
        ) + "px";
      tip.style.top =
        Math.max(
          8,
          Math.min(
            pointer.y - rect.top + 16,
            rect.height - tip.offsetHeight - 8,
          ),
        ) + "px";
    }
    const raycaster = new THREE.Raycaster(),
      mouse = new THREE.Vector2();
    function pick() {
      hoverRequest = null;
      if (!pointer) return;
      const r = renderer.domElement.getBoundingClientRect();
      mouse.set(
        ((pointer.x - r.left) / r.width) * 2 - 1,
        (-(pointer.y - r.top) / r.height) * 2 + 1,
      );
      scene.updateMatrixWorld(true);
      raycaster.setFromCamera(mouse, camera);
      const hit = raycaster.intersectObjects(
        meshes.filter((m) => m.visible),
        false,
      )[0];
      if (hit) {
        hovered = hit.object.userData.index;
        tooltip(hovered);
      } else hideTooltip();
    }
    renderer.domElement.addEventListener("pointermove", (e) => {
      pointer = { x: e.clientX, y: e.clientY };
      if (e.buttons) {
        hideTooltip();
        return;
      }
      if (hoverRequest === null) hoverRequest = requestAnimationFrame(pick);
    });
    renderer.domElement.addEventListener("pointerleave", () => {
      pointer = null;
      hideTooltip();
    });
    function table() {
      const rows = $("values").rows;
      for (let i = 0; i < count; i++) {
        for (let k = 0; k < (data.mode === "2d" ? 1 : 3); k++) {
          rows[i].cells[k + 2].textContent = fmt(values[i * 3 + k]);
        }
        if (data.hasEstimates) {
          rows[i].cells[data.mode === "2d" ? 3 : 5].textContent = valueStatuses[
            i
          ].replaceAll("_", " ");
        }
      }
      $("table-period").textContent =
        label + (observation ? "" : " · interpolated");
    }
    function draw(position) {
      framePosition = Math.max(0, Math.min(data.frameCount - 1, position));
      index = Math.floor(framePosition);
      const next = Math.min(index + 1, data.frameCount - 1),
        mix = framePosition - index,
        lerp = (a, b) => a + (b - a) * mix,
        total = lerp(totals[index], totals[next]);
      currentTotal = 0;
      for (let j = 0; j < count; j++) {
        const left = data.observations[index][j],
          right = data.observations[next][j],
          a =
            data.normalization === "shares"
              ? lerp(left[0] / totals[index], right[0] / totals[next]) * total
              : lerp(left[0], right[0]),
          b = lerp(left[2], right[2]),
          ratio = b / a;
        values[j * 3] = a;
        values[j * 3 + 1] = ratio;
        values[j * 3 + 2] = b;
        const leftFrame = data.frames[index],
          rightFrame = data.frames[next],
          estimate = lerp(estimateAt(leftFrame, j), estimateAt(rightFrame, j)),
          sourceFrame = mix < 0.5 ? leftFrame : rightFrame;
        let sourceYear =
          sourceFrame.progress < 0.5
            ? (sourceFrame.from ?? sourceFrame.year)
            : sourceFrame.year;
        if (estimate > 1e-8 && statusAt(sourceYear, j) === "observed") {
          sourceYear =
            sourceYear === sourceFrame.year
              ? (sourceFrame.from ?? sourceFrame.year)
              : sourceFrame.year;
        }
        // A neighbouring captured state may still carry the estimate at a year
        // boundary; retain its explanation while the grey colour fades out.
        if (estimate > 1e-8 && statusAt(sourceYear, j) === "observed") {
          sourceYear = [
            leftFrame.from,
            leftFrame.year,
            rightFrame.from,
            rightFrame.year,
          ].find((year) => statusAt(year, j) !== "observed");
        }
        estimateWeights[j] = estimate;
        valueStatuses[j] =
          estimate > 1e-8 ? statusAt(sourceYear, j) : "observed";
        valueNotes[j] = data.valueNotes?.[String(sourceYear)]?.[j] || "";
        currentTotal += a;
      }
      for (let k = 0; k < meshCount; k++) {
        const j = data.meshes[k].region,
          offset = (index * meshCount + k) * 4,
          nextOffset = (next * meshCount + k) * 4,
          x = lerp(transforms[offset], transforms[nextOffset]),
          y = lerp(transforms[offset + 1], transforms[nextOffset + 1]),
          s = Math.sqrt(
            lerp(transforms[offset + 2] ** 2, transforms[nextOffset + 2] ** 2),
          ),
          h = values[j * 3 + 1] * data.heightScale,
          mesh = meshes[k],
          flat = top || h === 0 || data.mode === "2d";
        flatMaterials[k].color
          .copy(baseColors[j])
          .lerp(estimatedColor, estimateWeights[j]);
        translucentMaterials[k].color.copy(flatMaterials[k].color);
        mesh.geometry = flat ? flats[k] : solids[k];
        mesh.material = flat ? flatMaterials[k] : translucentMaterials[k];
        mesh.position.set(x - cx, y - cy, 0);
        mesh.scale.set(s, s, flat ? 1 : h);
        mesh.layers.set(flat ? 0 : 1);
        mesh.updateMatrix();
      }
      $("stamp").textContent =
        data.frameCount === 1 ? "Explore the map" : label;
      $("scrub").value = time;
      $("previous").disabled = time === marks[0].time;
      $("next").disabled = time === marks.at(-1).time;
      if (hovered !== null && pointer) tooltip(hovered);
      if (document.querySelector("details").open) table();
      invalidate();
    }
    function seek(value) {
      time = Math.max(marks[0].time, Math.min(marks.at(-1).time, value));
      let segment = 0;
      while (segment + 1 < marks.length && time >= marks[segment + 1].time)
        segment++;
      const left = marks[segment],
        right = marks[Math.min(segment + 1, marks.length - 1)],
        fraction =
          right.time === left.time
            ? 0
            : (time - left.time) / (right.time - left.time);
      observation = fraction < 1e-9;
      label = observation ? left.label : left.label + " → " + right.label;
      $("year").value = left.time;
      if (observation) draw(left.frame);
      else {
        let low = left.frame,
          high = right.frame;
        while (high - low > 1) {
          const middle = Math.floor((low + high) / 2);
          if (frameTimes[middle] <= time) low = middle;
          else high = middle;
        }
        const span = frameTimes[high] - frameTimes[low];
        draw(low + (span > 0 ? (time - frameTimes[low]) / span : 0));
      }
    }
    function stop() {
      playing = false;
      $("play").textContent = "Play";
      if (raf !== null) cancelAnimationFrame(raf);
      raf = null;
    }
    function tick(now) {
      if (!playing) return;
      // Wall time controls calendar progress, regardless of solver frame count.
      const elapsed = Math.min((now - last) / 1000, 0.25);
      last = now;
      seek(time + elapsed * Number($("speed").value));
      if (time === marks.at(-1).time) stop();
      else raf = requestAnimationFrame(tick);
    }
    $("play").onclick = () => {
      if (playing) {
        stop();
        return;
      }
      if (time === marks.at(-1).time) seek(marks[0].time);
      playing = true;
      last = performance.now();
      $("play").textContent = "Pause";
      raf = requestAnimationFrame(tick);
    };
    $("scrub").min = marks[0].time;
    $("scrub").max = marks.at(-1).time;
    $("scrub").oninput = () => {
      stop();
      seek(Number($("scrub").value));
    };
    $("previous").onclick = () => {
      stop();
      seek(
        marks.filter((m) => m.time < time - 1e-9).at(-1)?.time ?? marks[0].time,
      );
    };
    $("next").onclick = () => {
      stop();
      seek(marks.find((m) => m.time > time + 1e-9)?.time ?? marks.at(-1).time);
    };
    for (const [i, mark] of marks.entries()) {
      const option = document.createElement("option");
      option.value = mark.time;
      option.textContent = mark.label;
      $("year").append(option);
      if (
        i === 0 ||
        i === marks.length - 1 ||
        (calendar && mark.time % 10 === 0)
      ) {
        $("year-ticks").append(option.cloneNode(true));
      }
    }
    $("year").onchange = () => {
      stop();
      seek(Number($("year").value));
    };
    $("range-start").textContent = marks[0].label;
    $("range-end").textContent = marks.at(-1).label;
    $("speed-label").title = calendar
      ? "1× = one year per second"
      : "1× = one observation interval per second";
    $("top").onclick = () => view(true);
    $("view3d").onclick = () => view(false);
    $("reset").onclick = () => view(data.mode === "2d");
    $("save").onclick = () => {
      render();
      renderer.domElement.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob),
          a = document.createElement("a");
        a.href = url;
        a.download = "cartogram-" + label.replace(/[^\w.-]+/g, "_") + ".png";
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
    };
    for (const [name, group] of groups) {
      const button = document.createElement("button"),
        dot = document.createElement("i");
      dot.style.background = group.color;
      button.append(dot, document.createTextNode(name));
      button.setAttribute("aria-pressed", "true");
      button.onclick = () => {
        group.visible = !group.visible;
        button.setAttribute("aria-pressed", String(group.visible));
        meshes.forEach((m) => {
          if (data.regions[m.userData.index].group === name)
            m.visible = group.visible;
        });
        hideTooltip();
        invalidate();
      };
      $("legend").append(button);
    }
    $("search").oninput = () => {
      const query = $("search").value.toLocaleLowerCase();
      meshes.forEach((m) => {
        const c = data.regions[m.userData.index],
          match = (c.name + " " + c.id).toLocaleLowerCase().includes(query);
        m.material.emissive.set(query && match ? 0x23443d : 0x000000);
      });
      invalidate();
    };
    document.querySelector("details").addEventListener("toggle", () => {
      if (document.querySelector("details").open) table();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
    });
    const temporal = data.frameCount > 1;
    [
      "timeline",
      "play",
      "previous",
      "next",
      "speed-label",
      "year-label",
    ].forEach((id) => ($(id).hidden = !temporal));
    if (data.mode === "2d") {
      $("view3d").hidden = true;
      $("top").hidden = true;
    }
    $("height-note").hidden = data.mode !== "3d";
    $("estimate-key").hidden = !data.hasEstimates;
    $("scale").textContent =
      data.mode === "3d"
        ? "Height scale: " +
          fmt(data.heightScale) +
          " chart units per ratio unit."
        : "";
    $("explanation").textContent =
      (temporal
        ? "Playback follows captured solver states continuously between annual inputs. "
        : "") +
      (data.normalization === "shares"
        ? "Each period has fixed total area; footprints encode shares of the included total."
        : "One fixed value-to-area scale is used.") +
      (data.mode === "3d"
        ? " Heights use one common scale across all periods."
        : "") +
      (data.hasEstimates
        ? " Missing annual values were filled before simulation; grey regions use estimates."
        : "");
    $("opacity").value = Math.round(opacity * 100);
    $("opacity").oninput = () =>
      updateOpacity(Number($("opacity").value) / 100);
    updateOpacity(opacity);
    resize();
    view(top);
    notice.hidden = true;
    // Read-only diagnostics for browser regression checks and performance inspection.
    window.cartogramViewer = {
      get frame() {
        return index;
      },
      get framePosition() {
        return framePosition;
      },
      get time() {
        return time;
      },
      get observation() {
        return observation;
      },
      observationCount: marks.length,
      frameCount: data.frameCount,
      meshCount,
      regionCount: count,
      get triangles() {
        return renderedTriangles;
      },
      get drawCalls() {
        return renderedDrawCalls;
      },
      material: "matte alpha transparency with low-frequency Perlin normals",
      transparency: oitAvailable
        ? "weighted blended order independent"
        : "alpha blending",
      get opacity() {
        return opacity;
      },
    };
  } catch (error) {
    notice.textContent =
      "Unable to display the map: " +
      error.message +
      " Region values are available below.";
    console.error(error);
  }
})();

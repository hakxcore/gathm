/* Local SVG icons. No network dependency or HTML interpolation. */
(function () {
    "use strict";
    const paths = {
        send: ["M22 2 9 15", "m22 2-7 20-6-7-7-6Z"],
        mic: ["M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0Z", "M5 10v2a7 7 0 0 0 14 0v-2", "M12 19v3M8 22h8"],
        radio: ["M12 11v2", "M8 8a6 6 0 0 0 0 8M16 8a6 6 0 0 1 0 8", "M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"],
        square: ["M5 5h14v14H5Z"],
        "arrow-up": ["M12 19V5", "m5 12 7-7 7 7"],
        "rotate-ccw": ["M3 10a9 9 0 1 1 2 8", "M3 4v6h6"],
        sparkles: ["m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"],
        sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8", "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"],
        "pen-line": ["m16 3 5 5-12 12-6 1 1-6Z", "m14 5 5 5M13 21h8"],
        lightbulb: ["M9 18h6m-6 3h6", "M9 15c0-2-3-3-3-7a6 6 0 0 1 12 0c0 4-3 5-3 7Z"],
        "volume-2": ["M11 5 6 9H3v6h3l5 4Z", "M15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"],
        "volume-x": ["M11 5 6 9H3v6h3l5 4Z", "m16 9 6 6m0-6-6 6"]
    };
    window.gathmIcons = function () {
        document.querySelectorAll("i[data-lucide]").forEach(function (node) {
            const definition = paths[node.getAttribute("data-lucide")];
            if (!definition) return;
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
                "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
                "aria-hidden": "true", width: "24", height: "24", class: node.className
            }).forEach(([key, value]) => svg.setAttribute(key, value));
            definition.forEach(function (d) {
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", d);
                svg.appendChild(path);
            });
            node.replaceWith(svg);
        });
    };
}());

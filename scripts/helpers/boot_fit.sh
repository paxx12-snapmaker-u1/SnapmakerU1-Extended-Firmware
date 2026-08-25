#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")/.."

if ! command -v dumpimage &> /dev/null; then
    echo "Error: dumpimage not found. Install u-boot-tools"
    exit 1
fi
if ! command -v mkimage &> /dev/null; then
    echo "Error: mkimage not found. Install u-boot-tools"
    exit 1
fi

show_usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  extract <boot.img> <output_dir>    Extract FIT image components"
    echo "  repack <input_dir> <boot.img>      Repack FIT image from components"
    echo "  list <boot.img>                    List FIT image contents"
    echo "  self-test <boot.img> <output_dir>  Self-test FIT image"
    echo ""
    echo "Example:"
    echo "  $0 extract boot.img extracted/"
    echo "  $0 repack extracted/ boot-new.img"
}

extract_fit() {
    local boot_img="$1"
    local output_dir="$2"

    if [[ ! -f "$boot_img" ]]; then
        echo "Error: Boot image not found: $boot_img"
        exit 1
    fi

    mkdir -p "$output_dir"

    echo "=== Extracting FIT image: $boot_img ==="
    local fit_info=$(dumpimage -l "$boot_img" 2>&1)
    echo "$fit_info"

    echo ""
    echo "Validating FIT structure..."

    local num_images=$(echo "$fit_info" | grep -c "^ Image [0-9]")
    if [[ "$num_images" -ne 3 ]]; then
        echo "Error: Expected 3 images, found $num_images"
        exit 1
    fi

    local img0_type=$(echo "$fit_info" | grep "^ Image 0 " | sed 's/.*(\(.*\)).*/\1/')
    local img1_type=$(echo "$fit_info" | grep "^ Image 1 " | sed 's/.*(\(.*\)).*/\1/')
    local img2_type=$(echo "$fit_info" | grep "^ Image 2 " | sed 's/.*(\(.*\)).*/\1/')

    if [[ "$img0_type" != "fdt" ]]; then
        echo "Error: Image 0 is '$img0_type', expected 'fdt'"
        exit 1
    fi
    if [[ "$img1_type" != "kernel" ]]; then
        echo "Error: Image 1 is '$img1_type', expected 'kernel'"
        exit 1
    fi
    if [[ "$img2_type" != "resource" ]]; then
        echo "Error: Image 2 is '$img2_type', expected 'resource'"
        exit 1
    fi

    echo "  [✓] Image 0: fdt"
    echo "  [✓] Image 1: kernel"
    echo "  [✓] Image 2: resource"

    echo ""
    echo "Extracting components..."

    dumpimage -T flat_dt -p 0 -o "$output_dir/fdt.dtb" "$boot_img"
    echo "  [✓] FDT extracted to $output_dir/fdt.dtb"

    dumpimage -T flat_dt -p 1 -o "$output_dir/kernel.img" "$boot_img"
    echo "  [✓] Kernel extracted to $output_dir/kernel.img"

    dumpimage -T flat_dt -p 2 -o "$output_dir/resource.img" "$boot_img"
    echo "  [✓] Resource extracted to $output_dir/resource.img"

    echo ""
    echo "Creating its file for repacking..."
    cat > "$output_dir/boot.its" << 'EOF'
/dts-v1/;

/ {
    version = <0x00>;
    description = "U-Boot FIT source file for arm";

    images {
        fdt {
            data = /incbin/("fdt.dtb");
            type = "flat_dt";
            arch = "arm64";
            compression = "none";
            load = <0xffffff00>;
            data-position = <0x800>;
            hash {
                algo = "sha256";
            };
        };

        kernel {
            data = /incbin/("kernel.img");
            type = "kernel";
            arch = "arm64";
            os = "linux";
            compression = "lz4";
            load = <0xffffff01>;
            entry = <0xffffff01>;
            hash {
                algo = "sha256";
            };
        };

        resource {
            data = /incbin/("resource.img");
            type = "multi";
            arch = "arm64";
            compression = "none";
            hash {
                algo = "sha256";
            };
        };
    };

    configurations {
        default = "conf";
        conf {
            rollback-index = <0>;
            fdt = "fdt";
            kernel = "kernel";
            multi = "resource";
        };
    };
};
EOF

    echo "  [✓] ITS file created at $output_dir/boot.its"
    echo ""
    echo "Extraction complete!"
}

repack_fit() {
    local input_dir="$1"
    local boot_img="$2"

    if [[ ! -d "$input_dir" ]]; then
        echo "Error: Input directory not found: $input_dir"
        exit 1
    fi

    if [[ ! -f "$input_dir/boot.its" ]]; then
        echo "Error: boot.its not found in $input_dir"
        exit 1
    fi

    if [[ ! -f "$input_dir/fdt.dtb" ]]; then
        echo "Error: fdt.dtb not found in $input_dir"
        exit 1
    fi

    if [[ ! -f "$input_dir/kernel.img" ]]; then
        echo "Error: kernel.img not found in $input_dir"
        exit 1
    fi

    if [[ ! -f "$input_dir/resource.img" ]]; then
        echo "Error: resource.img not found in $input_dir"
        exit 1
    fi

    echo "=== Repacking FIT image ==="

    boot_img=$(realpath "$boot_img")
    cd "$input_dir"
    mkimage -E -p 0x800 -B 0x200 -f boot.its "$boot_img"
    cd - > /dev/null

    echo ""
    echo "Repack complete: $boot_img"
}

list_fit() {
    local boot_img="$1"

    if [[ ! -f "$boot_img" ]]; then
        echo "Error: Boot image not found: $boot_img"
        exit 1
    fi

    dumpimage -l "$boot_img"
}

selftest_fit() {
    local boot_img="$1"
    local output_dir="$2"
    local repack_img="$output_dir/boot-repacked.img"

    extract_fit "$boot_img" "$output_dir"
    extract_resource "$output_dir/resource.img" "$output_dir/resources/"
    cp "$output_dir/resource.img" "$output_dir/resource.img.org"
    repack_resources "$output_dir/resources/" "$output_dir/resource.img"
    repack_fit "$output_dir" "$repack_img"

    echo "Original FIT image contents:"
    list_fit "$boot_img"
    echo ""
    echo "Repacked FIT image contents:"
    list_fit "$repack_img"
    echo ""
    echo "Comparing original and repacked images..."
    diff -U 1 <(dumpimage -l "$boot_img") <(dumpimage -l "$repack_img") || true
    echo "Self-test complete: repacked image is $repack_img"
}

extract_resource() {
    local resource_img="$(realpath "$1")"
    local output_dir="$2"

    if [[ ! -f "$resource_img" ]]; then
        echo "Error: Resource image not found: $resource_img"
        exit 1
    fi

    mkdir -p "$output_dir"
    pushd "$output_dir"
    "$PROJECT_ROOT/tools/resource_tool/resource_tool" --unpack --image="$resource_img"
    mv out/* .
    rm -rf out
    popd
    echo "Resource extraction complete!"
}

repack_resources() {
    local input_dir="$1"
    local resource_img="$(realpath "$2")"

    if [[ ! -d "$input_dir" ]]; then
        echo "Error: Input directory not found: $input_dir"
        exit 1
    fi

    pushd "$input_dir"
    "$PROJECT_ROOT/tools/resource_tool/resource_tool" --pack --image="$resource_img" \
        "rk-kernel.dtb" \
        "logo.bmp" \
        "logo_kernel.bmp"
    popd
    echo "Resource packing complete!"
}

case "$1" in
    extract)
        if [[ $# -ne 3 ]]; then
            show_usage
            exit 1
        fi
        extract_fit "$2" "$3"
        extract_resource "$3/resource.img" "$3/resources/"
        ;;
    repack)
        if [[ $# -ne 3 ]]; then
            show_usage
            exit 1
        fi
        repack_resources "$2/resources/" "$2/resource.img"
        repack_fit "$2" "$3"
        ;;
    self-test)
        if [[ $# -ne 3 ]]; then
            show_usage
            exit 1
        fi
        selftest_fit "$2" "$3"
        ;;
    list)
        if [[ $# -ne 2 ]]; then
            show_usage
            exit 1
        fi
        list_fit "$2"
        ;;
    *)
        show_usage
        exit 1
        ;;
esac

/*
 * Copyright (c) 2021 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import * as path from 'path';
import {
	LanguageClient,
	LanguageClientOptions,
	ServerOptions,
    TransportKind,
    Middleware
} from 'vscode-languageclient/node';

var client: LanguageClient

const ZEPHYR_BASE = '/home/trond/ncs/zephyr';
const SAMPLE_DIR = '/home/trond/ncs/nrf/samples/bluetooth/mesh/light';
const BOARD = 'nrf52dk_nrf52832';
const ARCH = 'arm';

export function activate(ctx: vscode.ExtensionContext) {

    const serverOptions: ServerOptions = {
        command: '/home/trond/.pyenv/shims/python3.6',
        args: [path.resolve(ctx.extensionPath, 'srv', 'kconfiglsp.py')],
        options: {
            cwd: SAMPLE_DIR,
            env: {
                ZEPHYR_BASE: ZEPHYR_BASE,
                ZEPHYR_TOOLCHAIN_VARIANT: 'zephyr',
                srctree: ZEPHYR_BASE,
                KERNELVERSION: '0x12334',
                KCONFIG_CONFIG: path.join(SAMPLE_DIR, 'build/zephyr/.config'),
                ARCH: ARCH,
                ARCH_DIR: path.join(ZEPHYR_BASE, 'arch'),
                BOARD_DIR: path.join(ZEPHYR_BASE, 'boards', ARCH, BOARD),
                KCONFIG_BINARY_DIR: path.join(SAMPLE_DIR, 'build', 'Kconfig'),
                TOOLCHAIN_KCONFIG_DIR: path.join(ZEPHYR_BASE, 'cmake', 'toolchain', 'zephyr'),
                ZEPHYR_MCUBOOT_MODULE_DIR: 'bootloader/mcuboot',
                ZEPHYR_MCUMGR_MODULE_DIR: 'modules/lib/mcumgr',
                ZEPHYR_NRFXLIB_MODULE_DIR: 'nrfxlib',
                ZEPHYR_CMOCK_MODULE_DIR: 'test/cmock',
                ZEPHYR_UNITY_MODULE_DIR: 'test/cmock/vendor/unity',
                ZEPHYR_MBEDTLS_NRF_MODULE_DIR: 'mbedtls',
                ZEPHYR_NANOPB_MODULE_DIR: 'modules/lib/nanopb',
                ZEPHYR_ALEXA_GADGETS_EMBEDDED_SAMPLE_CODE_MODULE_DIR: 'modules/alexa-embedded',
                ZEPHYR_CMSIS_MODULE_DIR: 'modules/hal/cmsis',
                ZEPHYR_CANOPENNODE_MODULE_DIR: 'modules/lib/canopennode',
                ZEPHYR_CI_TOOLS_MODULE_DIR: 'tools/ci-tools',
                ZEPHYR_CIVETWEB_MODULE_DIR: 'modules/lib/civetweb',
                ZEPHYR_FATFS_MODULE_DIR: 'modules/fs/fatfs',
                ZEPHYR_HAL_NORDIC_MODULE_DIR: 'modules/hal/nordic',
                ZEPHYR_HAL_ST_MODULE_DIR: 'modules/hal/st',
                ZEPHYR_LIBMETAL_MODULE_DIR: 'modules/hal/libmetal',
                ZEPHYR_LVGL_MODULE_DIR: 'modules/lib/gui/lvgl',
                ZEPHYR_MBEDTLS_MODULE_DIR: 'modules/crypto/mbedtls',
                ZEPHYR_NET_TOOLS_MODULE_DIR: 'tools/net-tools',
                ZEPHYR_OPEN_AMP_MODULE_DIR: 'modules/lib/open-amp',
                ZEPHYR_LORAMAC_NODE_MODULE_DIR: 'modules/lib/loramac-node',
                ZEPHYR_OPENTHREAD_MODULE_DIR: 'modules/lib/openthread',
                ZEPHYR_SEGGER_MODULE_DIR: 'modules/debug/segger',
                ZEPHYR_TINYCBOR_MODULE_DIR: 'modules/lib/tinycbor',
                ZEPHYR_TINYCRYPT_MODULE_DIR: 'modules/crypto/tinycrypt',
                ZEPHYR_LITTLEFS_MODULE_DIR: 'modules/fs/littlefs',
                ZEPHYR_MIPI_SYS_T_MODULE_DIR: 'modules/debug/mipi-sys-t',
                ZEPHYR_NRF_HW_MODELS_MODULE_DIR: 'modules/bsim_hw_models/nrf_hw_models',
                ZEPHYR_EDTT_MODULE_DIR: 'tools/edtt',
                ZEPHYR_TRUSTED_FIRMWARE_M_MODULE_DIR: 'modules/tee/tfm',
                ZEPHYR_TFM_MCUBOOT_MODULE_DIR: 'modules/tee/tfm-mcuboot',
                EDT_PICKLE: path.join(SAMPLE_DIR, 'zephyr', 'edt.pickle'),
                // KCONFIG_FUNCTIONS: path.join(ZEPHYR_BASE, 'scripts', 'kconfig', 'kconfigfunctions')
            },
        },
        transport: TransportKind.pipe,
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            {
                pattern: '**/*.conf',
            }
        ],
        diagnosticCollectionName: 'kconfig',
    };

    client = new LanguageClient('Zephyr Kconfig', serverOptions, clientOptions);
    client.start()
}
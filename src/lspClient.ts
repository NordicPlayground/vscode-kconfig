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
} from 'vscode-languageclient/node';
import { westEnv } from './zephyr';
import { existsSync, readFile } from 'fs';

var client: LanguageClient

const ZEPHYR_BASE = '/home/trond/ncs/zephyr';
const SAMPLE_DIR = '/home/trond/ncs/nrf/samples/bluetooth/mesh/light';
const BOARD = 'nrf52dk_nrf52832';
const ARCH = 'arm';

interface BoardConf {
    name: string;
    arch: string;
    dir: string;
}

interface WestModule {
    name: string;
    path: string;
}

class BuildConf {
    uri: vscode.Uri;
    board: BoardConf;
    confFiles: string[];
    zephyrBase: string;
    modules: WestModule[];
    constructor(uri: vscode.Uri, zephyrBase: string, board: BoardConf, confFiles: string[], modules: WestModule[]) {
        this.uri = uri;
        this.zephyrBase = zephyrBase;
        this.board = board;
        this.confFiles = confFiles;
        this.modules = modules;
    }

    static async fromBuildDir(uri: vscode.Uri) {
        const readCache = async () => {
            const cache = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(uri, 'CMakeCache.txt'))
            const lines = cache.getText().split('\n').filter(line => line.length > 0 && !line.startsWith('#') && !line.startsWith('//'));

            const entries = [
                'APPLICATION_SOURCE_DIR',
                'BOARD_DIR',
                'CACHED_BOARD',
                'CACHED_CONF_FILE',
                'CACHED_SHIELD',
                'ZEPHYR_BASE',
                'ZEPHYR_TOOLCHAIN_VARIANT',
            ];

            const values: {[name: string]: string | string[]} = {};
            lines.forEach(line => {
                const entry = <string>entries.find(e => line.startsWith(entry));
                if (entry) {
                    const name = entry.match(/^[^:=]+/)?.[0];
                    const val = entry.match(/=(.*)/)?.[1].trim();
                    if (!name || !val) {
                        return;
                    }

                    if (val.includes(';')) {
                        values[name] = val.split(';');
                    } else {
                        values[name] = val;
                    }
                }
            });

            // all of these are needed:
            if (Object.keys(values).length < entries.length) {
                return null;
            }

            return values;
        };

        const readModules = async () => {
            const module_list = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(uri, 'zephyr_modules.txt'));
            if (!module_list) {
                return;
            }

            const modules = new Array<WestModule>();

            module_list.getText().split('\n').forEach(line => {
                const match = line.match(/^"(.*)":"(.*)":".*"/);
                if (match) {
                    modules.push({name: match[1], path: match[2]});
                }
            });

            return modules;
        };

        const cache = await readCache();
        const modules = await readModules();
        if (!cache || !modules) {
            return null;
        }

        const board = <BoardConf>{name: cache['CACHED_BOARD'], arch: path.dirname(<string>cache['BOARD_DIR']), dir: <string>cache['BOARD_DIR']};
        const confFiles = Array.isArray(cache['CACHED_CONF_FILE']) ? cache['CACHED_CONF_FILE'] : [cache['CACHED_CONF_FILE']];

        return new BuildConf(vscode.Uri.file(<string>cache['APPLICATION_SOURCE_DIR']), <string>cache['ZEPHYR_BASE'], board, confFiles, modules);
    }
}

async function scanForBuilds() {
    return vscode.workspace.findFiles('CMakeCache.txt', null).then(uris => uris.map(async uri => await BuildConf.fromBuildDir(uri)).filter(Boolean));
}

export function activate(ctx: vscode.ExtensionContext) {

    vscode.workspace.findFiles('CMakeCache.txt', null);

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
                ZEPHYR_NRF_MODULE_DIR: 'nrf',
                ZEPHYR_MCUBOOT_MODULE_DIR: 'bootloader/mcuboot',
                ZEPHYR_MCUBOOT_KCONFIG: 'nrf/modules/mcuboot/Kconfig',
                ZEPHYR_NRFXLIB_MODULE_DIR: 'nrfxlib',
                ZEPHYR_TFM_MODULE_DIR: 'modules/tee/tfm',
                ZEPHYR_TFM_MCUBOOT_MODULE_DIR: 'modules/tee/tfm-mcuboot',
                ZEPHYR_CMSIS_MODULE_DIR: 'modules/hal/cmsis',
                ZEPHYR_CANOPENNODE_MODULE_DIR: 'modules/lib/canopennode',
                ZEPHYR_CIVETWEB_MODULE_DIR: 'modules/lib/civetweb',
                ZEPHYR_FATFS_MODULE_DIR: 'modules/fs/fatfs',
                ZEPHYR_HAL_NORDIC_MODULE_DIR: 'modules/hal/nordic',
                ZEPHYR_HAL_NORDIC_KCONFIG: 'zephyr/modules/hal_nordic/Kconfig',
                ZEPHYR_ST_MODULE_DIR: 'modules/hal/st',
                ZEPHYR_LIBMETAL_MODULE_DIR: 'modules/hal/libmetal',
                ZEPHYR_LVGL_MODULE_DIR: 'modules/lib/gui/lvgl',
                ZEPHYR_MBEDTLS_MODULE_DIR: 'modules/crypto/mbedtls',
                ZEPHYR_MCUMGR_MODULE_DIR: 'modules/lib/mcumgr',
                ZEPHYR_OPEN_AMP_MODULE_DIR: 'modules/lib/open-amp',
                ZEPHYR_LORAMAC_NODE_MODULE_DIR: 'modules/lib/loramac-node',
                ZEPHYR_OPENTHREAD_MODULE_DIR: 'modules/lib/openthread',
                ZEPHYR_SEGGER_MODULE_DIR: 'modules/debug/segger',
                ZEPHYR_TINYCBOR_MODULE_DIR: 'modules/lib/tinycbor',
                ZEPHYR_TINYCRYPT_MODULE_DIR: 'modules/crypto/tinycrypt',
                ZEPHYR_LITTLEFS_MODULE_DIR: 'modules/fs/littlefs',
                ZEPHYR_MIPI_SYS_T_MODULE_DIR: 'modules/debug/mipi-sys-t',
                ZEPHYR_NRF_HW_MODELS_MODULE_DIR: 'modules/bsim_hw_models/nrf_hw_models',
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

interface AddBuildParams {
    root: string;
    env: typeof process.env;
    conf: string[]
}

interface CMakeCache {
    [name: string]: string[];
}

function parseCmakeCache(uri: vscode.Uri): Promise<CMakeCache> {
    return new Promise<CMakeCache>((resolve, reject) => {
        readFile(uri.fsPath, {encoding: 'utf-8'}, (err, data) =>{
            if (err) {
                reject(err);
            } else {
                const lines = data.split(/\r?\n/g);
                const entries: CMakeCache = {};
                lines.forEach(line => {
                    const match = line.match(/^(\w+)(?::\w+)?\=(.*)/);
                    if (match) {
                        entries[match[1]] = match[2].trim().split(';');
                    }
                });

                resolve(entries);
            }
        })
    })
}

interface ZephyrModule {
    name: string;
    path: string;
}

function parseZephyrModules(uri: vscode.Uri): Promise<ZephyrModule[]> {
    return new Promise<ZephyrModule[]>((resolve, reject) => {
        readFile(uri.fsPath, {encoding: 'utf-8'}, (err, data) => {
            if (err) {
                reject(err);
            } else {
                const lines = data.split(/\r?\n/g);
                const modules = new Array<ZephyrModule>();
                lines.forEach(line => {
                    const match = line.match(/^"([^"]+)":"([^"]+)"/);
                    if (match) {
                        modules.push({
                            name: match[1],
                            path: match[2],
                        });
                    }
                });

                resolve(modules);
            }
        })
    })
}

interface BuildResponse {
    id: number;
}

export async function addBuild(uri: vscode.Uri) {
	const cache = await parseCmakeCache(vscode.Uri.joinPath(uri, 'CMakeCache.txt'));
	const modules = await parseZephyrModules(vscode.Uri.joinPath(uri, 'zephyr_modules.txt'));

	const board = cache['CACHED_BOARD'][0];
	const boardDir = cache['BOARD_DIR'][0];
	const arch = path.basename(path.dirname(boardDir));

	const appKconfig = path.join(cache['APPLICATION_SOURCE_DIR'][0], 'Kconfig');
	const zephyrKconfig = path.join(cache['ZEPHYR_BASE'][0], 'Kconfig');

	let root: string;
	if ('KCONFIG_ROOT' in cache) {
		root = cache['KCONFIG_ROOT'][0];
	} else if (existsSync(appKconfig)) {
		root = appKconfig;
	} else {
		root = zephyrKconfig;
	}

	const env: typeof process.env = {
		...westEnv,
		ZEPHYR_BASE: cache['ZEPHYR_BASE']?.[0],
		ZEPHYR_TOOLCHAIN_VARIANT: cache['ZE_HYR_TOOLCHAIN_VARIANT']?.[0],
		PYTHON_EXECUTABLE: cache['PYTHON_PREFER_EXECUTABLE']?.[0],
		srctree: cache['ZEPHYR_BASE']?.[0],
		// KERNELVERSION:
		KCONFIG_CONFIG: vscode.Uri.joinPath(uri, 'zephyr', '.config').fsPath,
		ARCH: arch,
		ARCH_DIR: path.join(cache['ZEPHYR_BASE'][0], 'arch'),
		BOARD_DIR: boardDir,
		KCONFIG_BINARY_DIR: vscode.Uri.joinPath(uri, 'Kconfig').fsPath,
		TOOLCHAIN_KCONFIG_DIR: path.join(
			cache['TOOLCHAIN_ROOT'][0],
			'cmake',
			'toolchain',
			cache['ZEPHYR_TOOLCHAIN_VARIANT'][0]
		),
		EDT_PICKLE: vscode.Uri.joinPath(uri, 'zephyr', 'edt.pickle').fsPath,
	};

	modules.forEach((module) => {
        const name = module.name.toUpperCase().replace(/[^\w]/g, '_');
		env[`ZEPHYR_${name}_MODULE_DIR`] = module.path;
		env[`ZEPHYR_${name}_KCONFIG`] = path.join(module.path, 'Kconfig');
	});

	Object.assign(env, {
		SHIELD_AS_LIST: cache['CACHED_SHIELD']?.join('\\;'),
		DTS_POST_CPP: vscode.Uri.joinPath(uri, 'zephyr', `${board}.dts.pre.tmp`).fsPath,
		DTS_ROOT_BINDINGS: cache['CACHED_DTS_ROOT_BINDINGS'].join('?'),

        // KCONFIG_FUNCTIONS: path.join(cache['ZEPHYR_BASE'][0], 'scripts', 'kconfig', 'kconfigfunctions')
	});

	return client.sendRequest<BuildResponse>('kconfig/addBuild', {
		root,
		env,
		conf: cache['CACHED_CONF_FILE'] ?? [],
	} as AddBuildParams);
}

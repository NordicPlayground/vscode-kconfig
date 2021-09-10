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

export async function activate(ctx: vscode.ExtensionContext) {
    vscode.commands.registerCommand('kconfig.add', () => {
        vscode.window
			.showOpenDialog({
				canSelectFolders: true,
				openLabel: 'Add',
				defaultUri: vscode.workspace.workspaceFolders?.[0].uri,
			})
			?.then((uris) => {
				if (uris) {
					addBuild(uris[0]);
				}
			});
    });

    const serverOptions: ServerOptions = {
        command: '/usr/bin/python',
        args: [path.resolve(ctx.extensionPath, 'srv', 'kconfiglsp.py')],
        options: {
            cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? process.cwd(),
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
    client.start();

    const caches = await vscode.workspace.findFiles(
		'**/CMakeCache.txt',
		'**/{twister,sanity}-out*'
	);

    await client.onReady();

    caches.map((cache) =>
		addBuild(vscode.Uri.parse(path.dirname(cache.fsPath))).catch((err) => {
			/* Ignore */
		})
	);

    const cacheWatcher = vscode.workspace.createFileSystemWatcher('**/CMakeCache.txt');

    cacheWatcher.onDidChange(addBuild);
    cacheWatcher.onDidCreate(addBuild);
    cacheWatcher.onDidDelete(removeBuild);
}

export function setMainBuild(uri?: vscode.Uri) {
    client.sendNotification('kconfig/setMainBuild', {uri: uri?.toString() ?? ''});
}

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
    id: string;
}

export async function addBuild(uri: vscode.Uri) {
	const cache = await parseCmakeCache(vscode.Uri.joinPath(uri, 'CMakeCache.txt'));
	const modules = await parseZephyrModules(vscode.Uri.joinPath(uri, 'zephyr_modules.txt'));

	const board = cache['CACHED_BOARD'][0];
	const boardDir = cache['BOARD_DIR'][0];
	const arch = path.basename(path.dirname(boardDir));

    const appDir = cache['APPLICATION_SOURCE_DIR'][0];
	const appKconfig = path.join(appDir, 'Kconfig');
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
		ZEPHYR_TOOLCHAIN_VARIANT: cache['ZEPHYR_TOOLCHAIN_VARIANT']?.[0],
		PYTHON_EXECUTABLE: cache['PYTHON_PREFER_EXECUTABLE']?.[0],
		srctree: cache['ZEPHYR_BASE']?.[0],
		// KERNELVERSION:
		KCONFIG_CONFIG: vscode.Uri.joinPath(uri, 'zephyr', '.config').fsPath,
		ARCH: arch,
		ARCH_DIR: path.join(cache['ZEPHYR_BASE'][0], 'arch'),
		BOARD: board,
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
        uri: uri.toString(),
		root,
		env,
		conf: cache['CACHED_CONF_FILE']?.map(file => path.resolve(appDir, file)) ?? [],
	} as AddBuildParams);
}

export async function removeBuild(uri: vscode.Uri) {
    client.sendNotification('kconfig/removeBuild', { uri: uri.toString() });
}

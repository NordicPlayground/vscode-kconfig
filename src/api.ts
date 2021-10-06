/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import { startExtension } from './extension';
import * as lsp from './lsp';

class Api {
    public version = 3;

    async activate(zephyrBase: vscode.Uri, _: string, env?: typeof process.env): Promise<boolean> {
        zephyr.setZephyrBase(zephyrBase);
        lsp.setWestEnv(env);
        startExtension();
        return true;
    }

    setConfig(config?: vscode.Uri): void {
        lsp.setMainBuild(config);
    }
}

export default Api;

/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import { KconfigLangHandler } from './extension';
import * as zephyr from './zephyr';
import * as kEnv from './env';

interface Context {
    /** Context ID number. Can be used to manipulate the context later. */
    id: number;
    /** Name of the context. */
    name: string;
    /**
     * The current build configuration.
     */
    buildConfig: vscode.Uri;
}

class Api {
    public version = 1;

    public activationCfg: {
        kconfigRoot: vscode.Uri | undefined;
        zephyrBoard: string | undefined;
        west: string | undefined;
    } = { kconfigRoot: undefined, zephyrBoard: undefined, west: undefined };

    async addContext(buildConfig: vscode.Uri, name?: string): Promise<void> {}

    async removeContext(id: number) {}

    async setZephyrBase(uri: vscode.Uri): Promise<void> {
        return zephyr.setZephyrBase(uri);
    }

    setZephyrBoard(board: string): void {
        return zephyr.updateBoardFromName(board);
    }

    setKconfigRoot(appUri: vscode.Uri): void {
        const root = kEnv.findRootFromApp(appUri);
        kEnv.setConfig('root', root);
    }

    setWest(uri: vscode.Uri): void {
        return zephyr.setWest(uri);
    }

    async getDetails(id: number) {}
}

export default Api;

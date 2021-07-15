/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import * as kEnv from './env';

interface Context {
    config: Config;
}

interface Config { 
    zephyrBase: vscode.Uri;
    west: vscode.Uri;
    appUri: vscode.Uri;
    zephyrBoard?: string;
}

class Api {
    public version = 1;

    async addContext(config: Config): Promise<Context> {
        return {
            config,
        }
    }

    async setContext(context: Context): Promise<void> {
        await zephyr.setZephyrBase(context.config.zephyrBase);
        await zephyr.setWest(context.config.west);
        const root = kEnv.findRootFromApp(context.config.appUri);
        kEnv.setConfig('root', root);
        if (context.config.zephyrBoard){
            zephyr.updateBoardFromName(context.config.zephyrBoard);
        }
    }

    async setZephyrBase(uri: vscode.Uri): Promise<void> {
        await zephyr.setZephyrBase(uri);
    } 

    async setWest(uri: vscode.Uri): Promise<void> {
        await zephyr.setWest(uri);
    }
}

export default Api;

/*
 * Copyright (c) 2021 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import {
	LanguageClient,
	LanguageClientOptions,
	ServerOptions,
	TransportKind
} from 'vscode-languageclient/node';

const serverOptions: ServerOptions = {
    command: 'python',
    args: ['kconfiglsp.py'],
    options: {
        cwd: '/home/trond/ncs/zephyr/scripts/kconfig',
    },
    transport: TransportKind.pipe,
};

const clientOptions: LanguageClientOptions = {
    documentSelector: [
        {
            pattern: '**/*.conf',
        }
    ],
};

export var client = new LanguageClient('Zephyr Kconfig', serverOptions, clientOptions);
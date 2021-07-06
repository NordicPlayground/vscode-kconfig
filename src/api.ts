import * as vscode from 'vscode';

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

    async addContext(buildConfig: vscode.Uri, name?: string): Promise<void> {}

    async setZephyrBase(uri: vscode.Uri) {
        
    }

    async setZephryBoard() {}

    async setWest(uri: vscode.Uri) {}

    async removeContext(id: number) {}

    async getDetails(id: number) {}
}

export default Api;

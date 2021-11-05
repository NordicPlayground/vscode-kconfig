export type RemoteFunctionSignatures = {};

type RemoteFunctionSignature = {
    [key in keyof RemoteFunctionSignatures]?: RemoteFunctionSignatures[key];
};

export type RemoteFunctionProvider = RemoteFunctionSignature;

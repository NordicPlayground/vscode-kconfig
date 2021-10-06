module.exports = {
    singleQuote: true,
    tabWidth: 4,
    useTabs: true,
    printWidth: 100,
    overrides: [
        {
            files: ['*.ts', '*.tsx'],
            options: {
                parser: 'typescript',
            },
        },
    ],
};

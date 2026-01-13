const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');
const Dotenv = require('dotenv-webpack');

module.exports = {
  mode: 'development',
  entry: "./webui/src/index.tsx",
  devtool: 'source-map',
  module: {
    rules: [
      {
        test: /\.js$/,
        enforce: "pre",
        use: ["source-map-loader"],
      },      
      {
        test: /\.tsx?$/,
        use: [ 'ts-loader' ],
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },  
      {
        test: /\.(woff(2)?|ttf|eot|svg)(\?v=\d+\.\d+\.\d+)?$/,
        type: 'asset/resource',
        generator: {
          filename: 'fonts/[name][ext]'
        }
      }
    ],
  },
  target: 'web',
  resolve: {
    extensions: ['.tsx', '.ts', '.js'],
    alias: {
      'artifacts/clients/typescript': path.resolve(__dirname, 'artifacts/clients/typescript/src'),
    },
  },
  output: {
    filename: '[name].js',
    path: path.resolve(__dirname, 'web'),
  },
  plugins: [
    new Dotenv({ systemvars: true, silent: true }),
    new CopyPlugin({
      patterns: [
        { from: 'webui/src/assets', to: 'assets', noErrorOnMissing: true }, // copy all assets
        { from: '**/*.html', to: '[path][name][ext]', context: 'webui/src/' }, // copy all HTML files with directory structure
      ],
    })
  ],
};
module.exports = {
  plugins: {
    '@fullhuman/postcss-purgecss': {
      content: ['themes/NowUI-Pro/layouts/**/*.html','content/**/*.html', 'content/**/*.md'],
      safelist: {
      greedy: ["/.animate.*/"]
      },
    fontFace: false,
    variables: false
    },
    autoprefixer: {},
    cssnano: { preset: 'default' }
  }
};

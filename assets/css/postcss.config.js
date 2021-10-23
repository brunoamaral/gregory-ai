module.exports = {
  plugins: {
    '@fullhuman/postcss-purgecss': {
      content: ['layouts/**/*.html','themes/NowUI-Pro/layouts/**/*.html','content/**/*.html', 'content/**/*.md'],
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
